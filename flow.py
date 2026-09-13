"""flow.py — the full per-post chain, PER-TARGET bot discovery:
Download button (t.me/<LINK_BOT>?start=...) -> LINK_BOT link message ->
bypass group Open link -> LINK_BOT final message (t.me/<MEDIA_BOT>?start=...)
-> MEDIA_BOT videos+srt -> cover post + media to DB channel.

v20: LINK_BOT and MEDIA_BOT are no longer hardcoded from env vars — each
target channel's Download button ALREADY points at its own LINK_BOT, and
LINK_BOT's final reply names its own MEDIA_BOT. So targets that use
different bots (Hanime Alliance vs I-ANIME etc.) all work with a single
scraper session, and adding a new channel with a new bot pair needs
ZERO config — just /target and go."""
import asyncio, logging, time
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
        self.paused_ids = set()  # target ids paused via /pause <n> (synced from Mongo)

log = logging.getLogger("flow")

state = FlowState()


async def _link_btn_labels():
    """Labels that identify LINK_BOT's link button: the built-in default plus
    every custom label the owner added via /linkbutton (Mongo-backed, active
    immediately — no redeploy when the bot renames its buttons)."""
    custom = await DB.get_link_buttons()
    return [BTN_SHORT_LINK] + [b for b in custom if norm(b) != norm(BTN_SHORT_LINK)]

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
      Returns (url, button, bot_username) so callers can learn WHICH bot
      this deep link addressed (used for per-target LINK_BOT discovery).
    - other URL buttons: return (url, button, None).
    - callback buttons: real msg.click(). Returns (result, button, None)."""
    found = find_button(msg, needle)
    if not found:
        raise RuntimeError(f"button '{needle}' not found")
    r, c, b = found
    url = getattr(b, "url", None)
    if url:
        bot, payload = parse_tg_start(url)
        if bot and client is not None:
            await client.send_message(bot, f"/start {payload}" if payload else "/start")
            return url, b, bot
        return url, b, bot
    res = await msg.click(i=c, j=r)
    return res, b, None

async def _collect_media(client, entity, after_id, max_wait=90, quiet=5):
    """Collect videos + .srt documents arriving after after_id; stop after a
    `quiet`-second gap (albums/multi-video) or max_wait.
    v17 fix: the media bot posts its decorative stickers/text INSTANTLY but
    takes many seconds to upload the actual video files (hundreds of MB).
    A bare quiet-timer fired in the gap between the sticker burst and the
    first video upload, and the DB channel silently got ONLY the stickers.
    The quiet timer now only counts once at least one VIDEO has arrived,
    and a collection with ZERO videos raises instead of archiving junk."""
    media, top, last_seen = [], after_id, time.time()
    saw_video = False
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if state.abort:
            raise Abort()
        msgs = await client.get_messages(entity, limit=20, min_id=after_id)
        for m in sorted([m for m in msgs if m and m.id > top], key=lambda x: x.id):
            top = max(top, m.id)
            # collect EVERYTHING the media bot delivers — videos (any format),
            # srt files, images, stickers, other documents
            if m.media or m.photo or m.document or m.video or m.sticker:
                media.append(m)
                last_seen = time.time()
                if is_video_msg(m):
                    saw_video = True
        # quiet-gap break is armed ONLY after a video is in hand — otherwise
        # the fast sticker burst ends collection before videos finish uploading
        if media and saw_video and time.time() - last_seen > quiet:
            break
        await asyncio.sleep(POLL_INTERVAL)
    if media and not saw_video:
        raise RuntimeError(
            f"@{entity} sent {len(media)} message(s) but NO video within "
            f"{max_wait}s (stickers/text only) — not archiving this post")
    return media

def _peek_link_bot(msg):
    """Read the LINK_BOT username from the Download button's URL WITHOUT
    clicking it — so we know which chat to wait on before the button fires."""
    found = find_button(msg, BTN_DOWNLOAD)
    if not found:
        return None
    _, _, b = found
    url = getattr(b, "url", None) or ""
    bot, _ = parse_tg_start(url)
    return bot


async def process_post(client, cfg, msg):
    target, bypass, dbc = cfg["target_id"], cfg["bypass_id"], cfg["db_id"]

    # LINK_BOT is DISCOVERED from THIS post's Download button (per target).
    # The env-var FUBUKI_BOT stays only as a last-resort fallback for weird
    # posts where the button isn't a t.me deep link.
    link_bot = _peek_link_bot(msg) or FUBUKI_BOT
    if not link_bot:
        raise RuntimeError(
            "Download button carries no t.me/<bot>?start=... link and no "
            "FUBUKI_BOT fallback is set — cannot determine LINK_BOT for this post")
    log.info("post %s: LINK_BOT=@%s (from Download button)", msg.id, link_bot)

    # 1) click Download on the target post — WITH its start payload, so LINK_BOT
    #    serves the linked content instead of its generic welcome message
    state.stage = f"clicking Download (LINK_BOT=@{link_bot})"
    base_f = await _last_id(client, link_bot)
    _, _, followed_bot = await _follow_button(msg, BTN_DOWNLOAD, client)
    if followed_bot and followed_bot != link_bot:
        # button URL parsed differently than the peek — trust the follow result
        link_bot = followed_bot
        base_f = await _last_id(client, link_bot)

    await asyncio.sleep(STEP_DELAY)

    # 2) LINK_BOT sends the linked message (Short link button) — or a link in
    #    text; if it answered with the generic welcome, re-send /start once
    state.stage = f"waiting @{link_bot} short-link message"
    labels = await _link_btn_labels()
    fm = None
    for attempt in (1, 2):
        try:
            fm = await _wait_new(client, link_bot, base_f, WAIT_BOT_REPLY,
                                 need_button=labels)
        except TimeoutError:
            try:
                fm = await _wait_new(client, link_bot, base_f, 10, need_text="http")
            except TimeoutError:
                fm = None  # welcome-only round -> fall through to the retry nudge
        if fm:
            break
        if attempt == 1:
            await client.send_message(link_bot, "/start")  # nudge after welcome
            base_f = await _last_id(client, link_bot)
    if not fm:
        raise RuntimeError(
            f"@{link_bot} sent neither a link button nor a link "
            f"(wanted one of {labels} — add the new label with /linkbutton)")

    # 3) activate 'Short link' (or take the link straight from the text)
    state.stage = "getting short link"
    short_link = first_url(fm.text or "")
    if not short_link:
        res, b, _ = await _follow_button(fm, labels, client)
        short_link = getattr(b, "url", None) or (res if isinstance(res, str) and "http" in res else None)
    if not short_link:
        lm = await _wait_new(client, link_bot, await _last_id(client, link_bot),
                             WAIT_BOT_REPLY, need_text="http")
        short_link = first_url(lm.text)
    if not short_link:
        raise RuntimeError("could not capture short link")

    await asyncio.sleep(STEP_DELAY)

    # 4) send the link to the bypass group; wait for the tagged 'Open link' reply
    state.stage = "waiting bypass group Open link"
    sent = await client.send_message(bypass, short_link)
    bm = await _wait_new(client, bypass, sent.id, WAIT_BYPASS_REPLY, need_button=BTN_OPEN_LINK)

    # 5) click 'Open link' -> deep link back into LINK_BOT (SAME bot as step 2)
    state.stage = f"clicking Open link (back to @{link_bot})"
    # _follow_button already follows tg deep links (sends /start <payload>)
    # exactly ONCE — no second send here, that caused the double-link bug.
    base_f = await _last_id(client, link_bot)
    _, _, open_bot = await _follow_button(bm, BTN_OPEN_LINK, client)
    # some setups route Open link to a DIFFERENT bot than the Download one —
    # follow whichever the button actually points at
    if open_bot and open_bot != link_bot:
        log.info("post %s: Open link routes to @%s (not @%s) — switching",
                 msg.id, open_bot, link_bot)
        link_bot = open_bot
        base_f = await _last_id(client, link_bot)

    await asyncio.sleep(STEP_DELAY)

    # 6) LINK_BOT replies 'Here is your link https://t.me/<MEDIA_BOT>?start=...'
    state.stage = f"waiting @{link_bot} final link"
    fm2 = await _wait_new(client, link_bot, base_f, WAIT_BOT_REPLY, need_text="t.me/")
    bot, payload = parse_tg_start(fm2.text)
    if not bot:
        # last resort: env-var MEDIA_BOT (kept for backward compat)
        bot = MEDIA_BOT
        payload = None
        if not bot:
            raise RuntimeError(f"no t.me start link in @{link_bot} final message "
                               "and no MEDIA_BOT fallback set")
    log.info("post %s: MEDIA_BOT=@%s (from @%s final link)", msg.id, bot, link_bot)

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
    other = [m for m in media if not is_video_msg(m) and not is_srt_msg(m)]
    if not media:
        raise RuntimeError("bot sent no media")
    log.info("collected %d msg(s): %d video + %d srt + %d other",
             len(media), len(vids), len(srts), len(other))

    await asyncio.sleep(STEP_DELAY)

    # 8) DB channel: cover post FIRST, then videos + srt
    state.stage = "sending cover post to DB"
    await forwarder.send_cover(client, target, msg, dbc)
    state.stage = f"sending {len(vids)} video(s)+{len(srts)} srt+{len(other)} other to DB"
    await forwarder.send_media(client, bot, vids + srts + other, dbc)

    await DB.incr("posts_done"); await DB.incr("videos_sent", len(vids)); await DB.incr("srt_sent", len(srts))
    if other:
        await DB.incr("other_sent", len(other))
    await DB.set_last_post(target, msg.id)
    state.stage = "idle"
