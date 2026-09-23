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
import asyncio, logging, re, time
from telethon.tl.types import User
from config import (BTN_DOWNLOAD, BTN_SHORT_LINK, BTN_OPEN_LINK, FUBUKI_BOT, MEDIA_BOT,
                    ADMIN_USER_ID, WAIT_BOT_REPLY, WAIT_BYPASS_REPLY, POLL_INTERVAL, STEP_DELAY)
from scraper import (find_button, parse_tg_start, first_url, norm, is_video_msg, is_srt_msg,
                     match_domain_rule)  # v39: domain-based bypass routing
import db as DB
import forwarder

# any tg link (with or without scheme) — used to harvest URLs from a bot's
# formatted reply that may embed several links (Original + Bypassed + credits)
_TG_URL_RE = re.compile(r"(?:https?://)?t\.me/[A-Za-z0-9_]+(?:\?start=[A-Za-z0-9_\-]+)?")

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
        # v39: per-worker stages for parallel scraping — {worker_name: stage}.
        # The legacy `stage`/`current_post` fields still track the single-account
        # path (bot.py only sets them then), so /status keeps working either way.
        self.workers = {}

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


class BypassFailed(Exception):
    """One bypass endpoint didn't come back with a usable link (timeout,
    unresolvable, reply without a t.me deep link). process_post catches this
    and falls through to the ALT bypass endpoint (/altbypass)."""
    pass


async def _stage(name, txt):
    """v39: record a worker's stage. With a worker name (parallel mode) it
    goes to state.workers[name] so workers don't clobber each other; without
    one (single-account path) it updates the legacy global state.stage
    exactly like the original code, so /status behaves as before."""
    if name:
        state.workers[name] = txt
    else:
        state.stage = txt


async def _bypass_once(client, endpoint, short_link, link_bot, msg, worker_name=None):
    """Send short_link to ONE bypass endpoint and follow the result.
    - BOT endpoint (e.g. @dex_fekkyeww_bot): replies in DM with a formatted
      message — the bypassed link is the t.me/?start=... URL in its text.
    - GROUP endpoint: someone tags with an 'Open link' BUTTON — clicked.
    Returns (link_bot, base_f) — link_bot may CHANGE if the bypassed link /
    Open button addresses a different bot than the Download one.
    Raises BypassFailed (never Abort — aborts always propagate)."""
    try:
        ent = await client.get_entity(endpoint)
    except Exception as e:
        raise BypassFailed(f"can't resolve {endpoint}: {e}")
    is_bot = isinstance(ent, User) and getattr(ent, "bot", False)
    await _stage(worker_name, f"waiting bypass {'bot' if is_bot else 'group'} reply ({endpoint})")
    sent = await client.send_message(endpoint, short_link)
    try:
        if is_bot:
            bm = await _wait_new(client, endpoint, sent.id, WAIT_BYPASS_REPLY,
                                 need_text="t.me/")
        else:
            bm = await _wait_new(client, endpoint, sent.id, WAIT_BYPASS_REPLY,
                                 need_button=BTN_OPEN_LINK)
    except Abort:
        raise
    except Exception as e:
        raise BypassFailed(f"{endpoint}: {e}")

    if is_bot:
        # formatted reply, e.g. '◈ Bypassed Link ➤ https://t.me/<bot>?start=...'
        # — harvest every t.me URL; prefer the ?start= deep link (that IS the
        # bypassed one); '@credit' usernames are not t.me URLs so ignored.
        text = bm.text or bm.message or ""
        urls = _TG_URL_RE.findall(text)
        start_urls = [u for u in urls if "?start=" in u]
        bypassed_url = (start_urls or urls)[-1] if urls else None
        if not bypassed_url:
            raise BypassFailed(f"bypass bot {endpoint} replied with no t.me link")
        new_bot, payload_in = parse_tg_start(bypassed_url)
        if not new_bot:
            raise BypassFailed(f"bypass bot reply URL {bypassed_url} isn't a t.me start link")
        log.info("post %s: bypass bot %s returned deep link -> @%s",
                 msg.id, endpoint, new_bot)
        if new_bot != link_bot:
            link_bot = new_bot
        base_f = await _last_id(client, link_bot)
        await _stage(worker_name, f"opening bypassed link at @{link_bot}")
        await client.send_message(link_bot,
                                   f"/start {payload_in}" if payload_in else "/start")
        return link_bot, base_f

    # GROUP path — click the tagged 'Open link' button (original behavior)
    await _stage(worker_name, f"clicking Open link (back to @{link_bot})")
    base_f = await _last_id(client, link_bot)
    _, _, open_bot = await _follow_button(bm, BTN_OPEN_LINK, client)
    if open_bot and open_bot != link_bot:
        log.info("post %s: Open link routes to @%s (not @%s) — switching",
                 msg.id, open_bot, link_bot)
        link_bot = open_bot
        base_f = await _last_id(client, link_bot)
    return link_bot, base_f


async def _alert_admins(text):
    """v39: DM the ⚠️ alert to the owner (ADMIN_USER_ID) AND every admin from
    /addadmin, via the CONTROL BOT (botapi.bot) when it's online; falls back
    to the userbot's DM otherwise. One failure for one recipient never blocks
    the others. NOTE: a control bot can only DM users who have /start'ed it
    at least once — the owner has; added admins should too."""
    import botapi
    ctl = getattr(botapi, "bot", None)
    async def _dm(uid):
        if ctl is not None:
            try:
                await ctl.send_message(uid, text, parse_mode="md")
                return True
            except Exception as e:
                log.warning("control-bot alert DM to %s failed (%s) — userbot fallback", uid, e)
        try:
            client = state.scrape_client
            if client is not None:
                await client.send_message(uid, text, parse_mode="md")
                return True
        except Exception as e:
            log.warning("userbot alert DM to %s failed: %s", uid, e)
        return False
    sent = False
    if ADMIN_USER_ID:
        sent = await _dm(ADMIN_USER_ID) or sent
    for a in await DB.get_admins():
        if a != ADMIN_USER_ID:
            sent = await _dm(a) or sent
    if not sent:
        log.warning("admin alert could not be delivered to anyone: %s", text[:120])


async def _alert_bypass_failure(short_link, endpoint, worker_name, target, msg_id, reason):
    """v39: instant ⚠️ Bypass Failure Alert when a bypass endpoint fails its
    2nd attempt (or yields an invalid response), in the owner's format."""
    await _alert_admins(
        "⚠️ **Bypass Failure Alert**\n"
        f"• **Failed Link:** `{short_link}`\n"
        f"• **Bypass Bot:** `@{endpoint}`\n"
        f"• **Userbot Used:** `{worker_name or 'main'}`\n"
        f"• **Target Channel:** `{target}`\n"
        f"• **Post Msg ID:** `{msg_id}`\n"
        f"• **Reason:** {str(reason)[:200]}")
    log.warning("bypass failure alert sent: post %s link %s via %s", msg_id, short_link, endpoint)


async def _bypass_chain(client, short_link, link_bot, msg, cfg, target, worker_name=None):
    """v39: route the short link to its bypass endpoint and run it.
    ROUTING: if the link's domain matches a /domainbypass rule, ONLY that
    designated endpoint handles the link (other pool bots are NOT tried for
    it). Otherwise every pool bot from /bypass + /addbypass is tried in list
    order, then /altbypass as the last-resort fallback.
    RETRY: each endpoint gets 2 attempts (initial + 1 retry); on the 2nd
    failure an instant ⚠️ admin alert fires, then the chain moves on.
    Raises BypassFailed when every candidate failed."""
    rules = await DB.get_bypass_domains()
    designated = match_domain_rule(short_link, rules)
    if designated is not None:
        candidates = [designated]
        log.info("post %s: domain rule routes %s -> %s", msg.id, short_link, designated)
    else:
        candidates = list(await DB.get_bypass_pool())
        if not candidates and cfg.get("bypass_id") is not None:
            candidates = [cfg["bypass_id"]]  # safety net if pool migration hasn't run
        alt = cfg.get("alt_bypass_id")
        if alt is not None and alt not in candidates:
            candidates.append(alt)
    candidates = [c for c in candidates if c is not None]
    if not candidates:
        raise BypassFailed("no bypass endpoints configured (/bypass or /addbypass)")
    last_err = None
    for endpoint in candidates:
        for attempt in (1, 2):
            if state.abort:
                raise Abort()
            try:
                return await _bypass_once(client, endpoint, short_link,
                                          link_bot, msg, worker_name)
            except Abort:
                raise
            except BypassFailed as e:
                last_err = e
                log.warning("post %s: bypass %s attempt %d/2 failed (%s)",
                            msg.id, endpoint, attempt, e)
        await _alert_bypass_failure(short_link, endpoint, worker_name,
                                    target, msg.id, last_err)
        if designated is None:
            continue  # try the next pool bot
        break       # domain rule: ONLY the designated endpoint handles this link
    raise BypassFailed(f"all bypass endpoints failed (last: {last_err})")


async def _alert_admin(client, target, msg, reason):
    """DM the owner (ADMIN_USER_ID) when BOTH bypass endpoints failed —
    includes a tappable post link so it can be checked in one tap.
    v39: routed through _alert_admins so owner + added admins all get it."""
    tid = str(target)
    if tid.startswith("-100"):
        link = f"https://t.me/c/{tid[4:]}/{msg.id}"
    else:
        link = f"https://t.me/{tid.lstrip('@')}/{msg.id}"
    await _alert_admins(
        f"\U0001F6A8 BYPASS FAILED — post needs attention\n"
        f"Target: {target}\nPost: {link}\nReason: {reason}")
    log.info("admin alerted for post %s", msg.id)


async def _collect_media(client, entity, after_id, max_wait=90, quiet=5):
    """Collect EVERYTHING the media bot sends after after_id — videos, srt,
    photos, stickers, AND text-only messages — stopping after a `quiet`-second
    gap or max_wait. The only thing excluded is our own '/start' trigger.
    v17 fix: the media bot posts its decorative stickers/text INSTANTLY but
    takes many seconds to upload the actual video files (hundreds of MB).
    A bare quiet-timer fired in the gap between the sticker burst and the
    first video upload, and the DB channel silently got ONLY the stickers.
    The quiet timer now only counts once at least one VIDEO has arrived,
    and a collection with ZERO videos raises instead of archiving junk.
    v21: text messages collected too (owner wants a full mirror of the bot's
    response, including its notes/warnings like the 12h-deletion notice)."""
    media, top, last_seen = [], after_id, time.time()
    saw_video = False
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if state.abort:
            raise Abort()
        msgs = await client.get_messages(entity, limit=20, min_id=after_id)
        for m in sorted([m for m in msgs if m and m.id > top], key=lambda x: x.id):
            top = max(top, m.id)
            # skip OUR OWN trigger message (/start <payload>) — everything
            # else the chat gained is the bot's delivery and gets mirrored
            if (not (m.media or m.photo or m.document or m.video or m.sticker)
                    and norm(m.text or "").startswith("/start")):
                continue
            # collect EVERYTHING: media messages AND text-only messages
            if (m.media or m.photo or m.document or m.video or m.sticker
                    or (m.text or "").strip()):
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


async def process_post(client, cfg, msg, worker_name=None):
    """worker_name (v39): set when running under the parallel dispatcher in
    bot.py — stages then go to state.workers[name] instead of the global
    state.stage so parallel workers don't clobber each other."""
    target, bypass, dbc = cfg["target_id"], cfg.get("bypass_id"), cfg["db_id"]

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

    # 4) v39: bypass ROUTING + RETRY + ALERT. The short link's domain picks
    #    its endpoint: a /domainbypass rule sends it ONLY to that bot; every
    #    other link tries each pool bot (/bypass + /addbypass) in order, then
    #    /altbypass as the last resort. Each endpoint gets 2 attempts; a 2nd
    #    failure fires an instant ⚠️ admin alert. If the whole chain fails,
    #    the owner gets the old tappable-post-link DM and the post raises, so
    #    the scrape loop skips it per the existing error policy.
    try:
        link_bot, base_f = await _bypass_chain(client, short_link, link_bot,
                                               msg, cfg, target, worker_name)
    except BypassFailed as e:
        await _alert_admin(client, target, msg, e)
        raise RuntimeError(f"all bypass endpoints failed ({e}) — admin alerted")

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
    # v32: skip EVERY image-with-caption the media bot sends. Target bots echo
    # the cover image (and sometimes extra image cards) with captions — the
    # real cover post is already delivered from the TARGET channel by
    # send_cover() below, which this filter never touches. An image = photo
    # OR image-mime document (spoiler images arrive as documents). Videos and
    # .srt are excluded above and are NEVER skipped; text-only notes stay;
    # caption-less images stay.
    other = []
    skipped_img = 0
    for m in media:
        if is_video_msg(m) or is_srt_msg(m):
            continue
        is_image = bool(getattr(m, "photo", None)) or (
            getattr(m, "document", None) is not None and
            (getattr(m.document, "mime_type", "") or "").lower().startswith("image/"))
        if is_image and (getattr(m, "message", "") or "").strip():
            skipped_img += 1
            continue
        other.append(m)
    if skipped_img:
        log.info("post %s: skipped %d bot image(s) with caption (cover comes from target channel)",
                 msg.id, skipped_img)
    if not media:
        raise RuntimeError("bot sent no media")
    log.info("collected %d msg(s): %d video + %d srt + %d other",
             len(media), len(vids), len(srts), len(other))

    await asyncio.sleep(STEP_DELAY)

    # 8) DB channel: cover post FIRST, then videos + srt — delivered as ONE
    #    serialized bundle (v39). With parallel userbots, the per-DB lock in
    #    forwarder.deliver_post_bundle guarantees this post's cover + all its
    #    media land together before the next post's bundle starts — no
    #    interleaving in DB, and DB2 inherits the same order via botapi's own
    #    per-DB mirror lock.
    bundle = vids + srts + other
    if worker_name:
        state.workers[worker_name] = f"delivering post {msg.id} (cover+{len(bundle)} media) to DB"
    else:
        state.stage = "sending cover post to DB"
    await forwarder.deliver_post_bundle(client, target, msg, bot, bundle, dbc)

    await DB.incr("posts_done"); await DB.incr("videos_sent", len(vids)); await DB.incr("srt_sent", len(srts))
    if other:
        await DB.incr("other_sent", len(other))
    await DB.set_last_post(target, msg.id)
    if worker_name:
        state.workers[worker_name] = "idle"
    else:
        state.stage = "idle"
