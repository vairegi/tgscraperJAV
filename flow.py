"""flow.py — the full per-post chain:
Download -> Fubuki Short link -> bypass group Open link -> Fubuki final link
-> Rias bot videos+srt -> cover post + media to DB channel."""
import asyncio, time
from config import (BTN_DOWNLOAD, BTN_SHORT_LINK, BTN_OPEN_LINK, FUBUKI_BOT, MEDIA_BOT,
                    WAIT_BOT_REPLY, WAIT_BYPASS_REPLY, POLL_INTERVAL, STEP_DELAY)
from scraper import find_button, parse_tg_start, first_url, norm, is_video_msg, is_srt_msg
import db as DB
import forwarder

class Abort(Exception):
    pass

class FlowState:
    def __init__(self):
        self.stage = "idle"
        self.current_post = None
        self.abort = False
        self.running = False
        self.paused = False
        self.started = False   # scraping runs ONLY after /start
        self.reset_gen = 0     # bumped by /reset and /goto -> aborts the current pass

state = FlowState()

async def _last_id(client, entity):
    msgs = await client.get_messages(entity, limit=1)
    return msgs[0].id if msgs else 0

async def _wait_new(client, entity, after_id, timeout, need_button=None, need_text=None):
    """Poll a chat for the oldest message with id > after_id that matches filters."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if state.abort:
            raise Abort()
        msgs = await client.get_messages(entity, limit=8, min_id=after_id)
        for m in sorted([m for m in msgs if m and m.id > after_id], key=lambda x: x.id):
            if need_button and not find_button(m, need_button):
                continue
            if need_text and norm(need_text) not in norm(m.text):
                continue
            return m
        await asyncio.sleep(POLL_INTERVAL)
    raise TimeoutError(f"no matching reply in chat {entity} within {timeout}s")

async def _follow_button(msg, needle, client=None):
    """Find a button by partial text and ACTIVATE it properly.
    - URL buttons deep-linking to a bot (t.me/<bot>?start=<payload>): send
      '/start <payload>' to that bot — a bare click fires plain /start, which
      only gets the bot's generic welcome instead of the linked content.
    - other URL buttons: return the URL.
    - callback buttons: real msg.click(). Returns (result, button)."""
    found = find_button(msg, needle)
    if not found:
        raise RuntimeError(f"button '{needle}' not found")
    r, c, b = found
    url = getattr(b, "url", None)
    if url:
        bot, payload = parse_tg_start(url)
        if bot and client is not None:
            await client.send_message(bot, f"/start {payload}" if payload else "/start")
            return url, b
        return url, b
    res = await msg.click(i=c, j=r)
    return res, b

async def _collect_media(client, entity, after_id, max_wait=90, quiet=5):
    """Collect videos + .srt documents arriving after after_id; stop after a
    `quiet`-second gap (albums/multi-video) or max_wait."""
    media, top, last_seen = [], after_id, time.time()
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if state.abort:
            raise Abort()
        msgs = await client.get_messages(entity, limit=20, min_id=after_id)
        for m in sorted([m for m in msgs if m and m.id > top], key=lambda x: x.id):
            top = max(top, m.id)
            if is_video_msg(m) or is_srt_msg(m):
                media.append(m)
                last_seen = time.time()
        if media and time.time() - last_seen > quiet:
            break
        await asyncio.sleep(POLL_INTERVAL)
    return media

async def process_post(client, cfg, msg):
    target, bypass, dbc = cfg["target_id"], cfg["bypass_id"], cfg["db_id"]

    # 1) click Download on the target post — WITH its start payload, so Fubuki
    #    serves the linked content instead of its generic welcome message
    state.stage = "clicking Download"
    base_f = await _last_id(client, FUBUKI_BOT)
    await _follow_button(msg, BTN_DOWNLOAD, client)

    await asyncio.sleep(STEP_DELAY)

    # 2) Fubuki sends the linked message (Short link button) — or a link in
    #    text; if it answered with the generic welcome, re-send /start once
    state.stage = "waiting Fubuki short-link message"
    fm = None
    for attempt in (1, 2):
        try:
            fm = await _wait_new(client, FUBUKI_BOT, base_f, WAIT_BOT_REPLY,
                                 need_button=BTN_SHORT_LINK)
        except TimeoutError:
            try:
                fm = await _wait_new(client, FUBUKI_BOT, base_f, 10, need_text="http")
            except TimeoutError:
                fm = None  # welcome-only round -> fall through to the retry nudge
        if fm:
            break
        if attempt == 1:
            await client.send_message(FUBUKI_BOT, "/start")  # nudge after welcome
            base_f = await _last_id(client, FUBUKI_BOT)
    if not fm:
        raise RuntimeError("Fubuki sent neither a Short link button nor a link")

    # 3) activate 'Short link' (or take the link straight from the text)
    state.stage = "getting short link"
    short_link = first_url(fm.text or "")
    if not short_link:
        res, b = await _follow_button(fm, BTN_SHORT_LINK, client)
        short_link = getattr(b, "url", None) or (res if isinstance(res, str) and "http" in res else None)
    if not short_link:
        lm = await _wait_new(client, FUBUKI_BOT, await _last_id(client, FUBUKI_BOT),
                             WAIT_BOT_REPLY, need_text="http")
        short_link = first_url(lm.text)
    if not short_link:
        raise RuntimeError("could not capture short link")

    await asyncio.sleep(STEP_DELAY)

    # 4) send the link to the bypass group; wait for the tagged 'Open link' reply
    state.stage = "waiting bypass group Open link"
    sent = await client.send_message(bypass, short_link)
    bm = await _wait_new(client, bypass, sent.id, WAIT_BYPASS_REPLY, need_button=BTN_OPEN_LINK)

    # 5) click 'Open link' -> deep link back into Fubuki
    state.stage = "clicking Open link"
    # _follow_button already follows tg deep links (sends /start <payload>)
    # exactly ONCE — no second send here, that caused the double-link bug.
    base_f = await _last_id(client, FUBUKI_BOT)
    await _follow_button(bm, BTN_OPEN_LINK, client)

    await asyncio.sleep(STEP_DELAY)

    # 6) Fubuki replies 'Here is your link https://t.me/Rias...?start=...'
    state.stage = "waiting Fubuki final link"
    fm2 = await _wait_new(client, FUBUKI_BOT, base_f, WAIT_BOT_REPLY, need_text="t.me/")
    bot, payload = parse_tg_start(fm2.text)
    if not bot:
        raise RuntimeError("no t.me start link in Fubuki final message")

    await asyncio.sleep(STEP_DELAY)

    # 7) open Rias bot -> collect video(s) + .srt
    state.stage = f"collecting media from @{bot}"
    base = await _last_id(client, bot)
    await client.send_message(bot, f"/start {payload}" if payload else "/start")
    media = await _collect_media(client, bot, base)
    # a video message has BOTH .video and .document — exclude videos from
    # the document list or every video gets sent twice (vid1,vid1,vid2,vid2)
    vids = [m for m in media if is_video_msg(m)]
    srts = [m for m in media if is_srt_msg(m)]
    if not vids and not srts:
        raise RuntimeError("bot sent no videos/srt")

    await asyncio.sleep(STEP_DELAY)

    # 8) DB channel: cover post FIRST, then videos + srt
    state.stage = "sending cover post to DB"
    await forwarder.send_cover(client, target, msg, dbc)
    state.stage = f"sending {len(vids)} video(s)+{len(srts)} srt to DB"
    await forwarder.send_media(client, bot, vids + srts, dbc)

    await DB.incr("posts_done"); await DB.incr("videos_sent", len(vids)); await DB.incr("srt_sent", len(srts))
    await DB.set_last_post(target, msg.id)
    state.stage = "idle"
