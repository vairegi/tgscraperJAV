"""flow.py — the full per-post chain:
Download -> Fubuki Short link -> bypass group Open link -> Fubuki final link
-> Rias bot videos+srt -> cover post + media to DB channel."""
import asyncio, time
from config import (BTN_DOWNLOAD, BTN_SHORT_LINK, BTN_OPEN_LINK, FUBUKI_BOT,
                    WAIT_BOT_REPLY, WAIT_BYPASS_REPLY, POLL_INTERVAL)
from scraper import find_button, parse_tg_start, first_url
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
            if need_text and need_text.lower() not in (m.text or "").lower():
                continue
            return m
        await asyncio.sleep(POLL_INTERVAL)
    raise TimeoutError(f"no matching reply in chat {entity} within {timeout}s")

async def _click(msg, needle):
    """Click a button by partial text. Returns (click_result, button).
    URL buttons return their URL string without opening anything."""
    found = find_button(msg, needle)
    if not found:
        raise RuntimeError(f"button '{needle}' not found")
    r, c, b = found
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
            if m.video or (m.document and (m.file.name or "").lower().endswith(".srt")):
                media.append(m)
                last_seen = time.time()
        if media and time.time() - last_seen > quiet:
            break
        await asyncio.sleep(POLL_INTERVAL)
    return media

async def process_post(client, cfg, msg):
    target, bypass, dbc = cfg["target_id"], cfg["bypass_id"], cfg["db_id"]

    # 1) click Download on the target post
    state.stage = "clicking Download"
    await _click(msg, BTN_DOWNLOAD)

    # 2) Fubuki sends a new message with a 'Short link' button
    state.stage = "waiting Fubuki short-link message"
    fm = await _wait_new(client, FUBUKI_BOT, await _last_id(client, FUBUKI_BOT),
                         WAIT_BOT_REPLY, need_button=BTN_SHORT_LINK)

    # 3) click 'Short link', capture the link
    state.stage = "clicking Short link"
    res, b = await _click(fm, BTN_SHORT_LINK)
    short_link = getattr(b, "url", None) or (res if isinstance(res, str) and "http" in res else None)
    if not short_link:
        lm = await _wait_new(client, FUBUKI_BOT, await _last_id(client, FUBUKI_BOT),
                             WAIT_BOT_REPLY, need_text="http")
        short_link = first_url(lm.text)
    if not short_link:
        raise RuntimeError("could not capture short link")

    # 4) send the link to the bypass group; wait for the tagged 'Open link' reply
    state.stage = "waiting bypass group Open link"
    sent = await client.send_message(bypass, short_link)
    bm = await _wait_new(client, bypass, sent.id, WAIT_BYPASS_REPLY, need_button=BTN_OPEN_LINK)

    # 5) click 'Open link' -> deep link back into Fubuki
    state.stage = "clicking Open link"
    res, b = await _click(bm, BTN_OPEN_LINK)
    open_url = getattr(b, "url", None) or (res if isinstance(res, str) else None)
    base_f = await _last_id(client, FUBUKI_BOT)
    if open_url:
        bot, payload = parse_tg_start(open_url)
        if bot:
            await client.send_message(bot, f"/start {payload}" if payload else "/start")

    # 6) Fubuki replies 'Here is your link https://t.me/Rias...?start=...'
    state.stage = "waiting Fubuki final link"
    fm2 = await _wait_new(client, FUBUKI_BOT, base_f, WAIT_BOT_REPLY, need_text="t.me/")
    bot, payload = parse_tg_start(fm2.text)
    if not bot:
        raise RuntimeError("no t.me start link in Fubuki final message")

    # 7) open Rias bot -> collect video(s) + .srt
    state.stage = f"collecting media from @{bot}"
    base = await _last_id(client, bot)
    await client.send_message(bot, f"/start {payload}" if payload else "/start")
    media = await _collect_media(client, bot, base)
    vids = [m for m in media if m.video]
    srts = [m for m in media if m.document]
    if not vids and not srts:
        raise RuntimeError("bot sent no videos/srt")

    # 8) DB channel: cover post FIRST, then videos + srt
    state.stage = "sending cover post to DB"
    await forwarder.send_cover(client, target, msg, dbc)
    state.stage = f"sending {len(vids)} video(s)+{len(srts)} srt to DB"
    await forwarder.send_media(client, bot, vids + srts, dbc)

    await DB.incr("posts_done"); await DB.incr("videos_sent", len(vids)); await DB.incr("srt_sent", len(srts))
    await DB.set_last_post(target, msg.id)
    state.stage = "idle"
