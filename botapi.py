"""botapi.py — control BOT (BOT_TOKEN from BotFather) with a tappable menu.
v13: per-target DB channels. /target wizard now asks for the channel id AND
then its DB channel; /setdb adjusts a target's DB later; /reset and /goto are
per-target; all wizards accept inline args (/adddb -100…); /help lists every
command. Only ADMIN_USER_ID can use it."""
from telethon import TelegramClient, events
from telethon.sessions import MemorySession
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault
from config import (API_ID, API_HASH, BOT_TOKEN, ADMIN_USER_ID, BTN_SHORT_LINK,
                    BULK_EDIT_DELAY, BULK_MAX_FLOOD, BULK_PROGRESS_EVERY)
from telethon.errors import FloodWaitError
import asyncio
import logging
import re
import shlex
import time
import random
import db as DB
import mtprotomgr as MTM
import richboard
from flow import state
from telethon.tl.types import Channel, Chat, User

bot = None  # created lazily inside start() (Py3.14 has no loop at import time)

_CMDS = [
    ("help",     "Show all commands"),
    ("ping",     "Check the bot is alive"),
    ("target",   "Add target channel + its DB channel (wizard)"),
    ("targets",  "Live charge-sheet board (table + buttons) — plain list: /targets_text"),
    ("targets_text", "Plain markdown targets list (fallback)"),
    ("deltarget","Remove a target channel"),
    ("setdb",    "Change a target's DB channel"),
    ("adddb",    "Set the fallback DB channel"),
    ("bypass",   "Set bypass endpoint (group OR bot @username)"),
    ("altbypass","Fallback bypass — used only if /bypass fails"),
    ("linkbutton", "List or add LINK_BOT button labels (no restart)"),
    ("removelinkbutton", "Remove a LINK_BOT button label by number"),
    ("goto",     "Set a target's start message (/goto <n> <msg> or link)"),
    ("reset",    "Reset a target's progress to post 1"),
    ("lastpost", "Newest post in a target channel"),
    ("start",    "Start scraping"),
    ("pause",    "Pause all (bare or /pause all), or one: /pause 2"),
    ("resume",   "Resume all (bare or /resume all), or one: /resume 2"),
    ("status",   "Live stage & config"),
    ("current",  "Current post & stage"),
    ("progress", "Stats, last post, failure reasons"),
    ("skip",     "Skip current post"),
    ("stop",     "Stop the scraper"),
    ("cancel",   "Cancel an active wizard prompt"),
    ("replace",  "Edit posts: /replace <ch> \"old\" \"new\" (userbot) — 2 args = DB2 (bot)"),
    ("deletetext","Remove text from channel posts: /deletetext <ch> \"text\""),
    ("massdlt",  "Delete a message range: /massdlt <chat> <start_link> <end_link>"),
    ("massdlt_status", "Mass-delete progress"),
    ("massdlt_stop", "Stop the mass-delete"),
    ("forward",  "Copy range to a channel: /forward <target> <source> <start> <end>"),
    ("forward_status", "Forward progress"),
    ("forward_stop", "Stop the forward (resumable)"),
    ("forward_resume", "Resume a stopped/interrupted forward"),
    ("add",      "Add bot(s) as admin: /add <channel> @bot1 @bot2 …"),
    ("addadmin", "Add a bot admin (bare /addadmin lists them)"),
    ("removeadmin", "Remove a bot admin: /removeadmin <user id>"),
    ("setdb2",   "Set a target's DB2 clean-mirror channel: /setdb2 <n> <id|off>"),
    ("avoidtext","DB2: strip a credit string: /avoidtext <n> \"multi word text\" (bare = list)"),
    ("removeavoid", "Remove an avoid string: /removeavoid <n> <#>"),
    ("checkdm",   "Auto admin pipeline: /checkdm on|off (userbot watches @richmining DMs)"),
]

_pending = {}  # user_id -> (kind, extra)
log_mirror = logging.getLogger("db2mirror")


async def _admin(uid):
    """The owner (ADMIN_USER_ID) always passes; ids added via /addadmin
    (Mongo-backed) also get full access."""
    if ADMIN_USER_ID == 0 or uid == ADMIN_USER_ID:
        return True
    return uid in await DB.get_admins()


async def _menu():
    await bot(SetBotCommandsRequest(
        scope=BotCommandScopeDefault(), lang_code="",
        commands=[BotCommand(c, d) for c, d in _CMDS]))


def _parse_id(raw):
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError:
        return raw.lstrip("@")


C_LINK = re.compile(r"(?:https?://)?t\.me/c/(\d+)(?:/\d+)?")


def _parse_chat_id(raw):
    """Numeric id, @username, or a t.me/c/<channel>[/<msg>] link -> chat id."""
    raw = raw.strip()
    m = C_LINK.search(raw)
    if m:
        return int("-100" + m.group(1))
    return _parse_id(raw)


def _not_a_channel_msg(v, kind="channel"):
    return (f"⚠️ {v} resolves to a USER/BOT, not a {kind} — nothing can be posted there.\n"
            "Easiest fix: copy ANY message link from the target chat "
            "(looks like https://t.me/c/1234567890/12) and paste that link instead of the id.")


def _entity_ok(ent, need):
    """need='channel' -> Channel only; need='group' -> Channel or Chat."""
    if need == "channel":
        return isinstance(ent, Channel)
    return isinstance(ent, (Channel, Chat))


async def _db_invite_link(client, db_id):
    """Tappable invite link for a DB channel, minted via the userbot (it is
    admin there) and cached in Mongo — no new link on every /targets call."""
    if not db_id or client is None:
        return None
    key = f"db_link_{db_id}"
    cached = await DB.get_config(key)
    if cached:
        return cached
    try:
        inv = await client(ExportChatInviteRequest(db_id))
    except Exception:
        return None  # no permission / private without invite rights -> plain id
    link = getattr(inv, "link", None)
    if link:
        await DB.set_config(key, link)
    return link


_TITLE_CACHE = {}  # chat_id -> title (titles rarely change)


async def _chat_title(client, chat_id):
    """Resolve a channel/group title via the userbot (it's a member there).
    Cached in-memory so repeat /targets calls don't spam get_entity."""
    if chat_id is None:
        return None
    if chat_id in _TITLE_CACHE:
        return _TITLE_CACHE[chat_id]
    title = None
    if client is not None:
        try:
            ent = await client.get_entity(chat_id)
            title = getattr(ent, "title", None) or getattr(ent, "first_name", None)
        except Exception:
            title = None
    if title:
        _TITLE_CACHE[chat_id] = title
    return title


def _c_link(chat_id, msg_id=None):
    """t.me/c/<internal>/<msg> — opens a PRIVATE channel for any member, no
    invite link needed. Needs a message id to be tappable, so callers pass
    the last scraped post when they have one."""
    if not msg_id:
        return None
    s = str(chat_id)
    if s.startswith("-100"):
        s = s[4:]
    return f"https://t.me/c/{s}/{msg_id}"


async def _chat_md(client, chat_id, msg_id=None, invite=None):
    """'[title](link)' markdown for a chat. Link priority: given invite link,
    then t.me/c/<id>/<msg_id>. Falls back to the plain title, then raw id."""
    title = await _chat_title(client, chat_id)
    label = (title or str(chat_id)).replace("[", "(").replace("]", ")").replace("\n", " ")
    link = invite or _c_link(chat_id, msg_id)
    return f"[{label}]({link})" if link else label


async def _pause_all_targets():
    """v36: pause the WHOLE scraper PERSISTENTLY and visibly.

    Bare /pause and /pause all used to set only the in-memory state.paused flag.
    If a post was already in flight the pass kept going, /targets showed no pause
    marker, and a restart cleared the flag — so it looked like "it didn't pause".
    We now ALSO flip every target's paused flag in MongoDB (identical to the proven
    /pause <n> path). The scrape loop's active-target filter then skips them all
    within seconds, /targets shows the pause marker, and the pause survives restarts.

    Returns (newly_paused_count, total_targets).
    """
    state.paused = True
    state.user_paused = True          # MANUAL pause: bulk-job auto-resume must not clear it
    targets = await DB.get_targets()
    flipped = 0
    for t in targets:
        if not t.get("paused"):
            await DB.set_target_paused(t["id"], True)
            flipped += 1
        state.paused_ids.add(t["id"])
    state.reset_gen += 1; state._last_scan = None   # drop any in-flight pass
    return flipped, len(targets)


async def _list_targets_text(client=None):
    targets = await DB.get_targets()
    if not targets:
        return None
    lines = ["🎯 Target channels:"]
    for i, t in enumerate(targets):
        prog = await DB.get_progress(t["id"])
        last = await DB.get_last_post(t["id"])
        # private targets have no invite link — embed the LAST SCRAPED post
        # link instead (works for any member of the channel)
        t_md = await _chat_md(client, t["id"], msg_id=last)
        if t["db_id"]:
            link = await _db_invite_link(client, t["db_id"])
            db_txt = await _chat_md(client, t["db_id"], invite=link)
        else:
            db_txt = "(fallback /adddb)"
        if t.get("db2_id"):
            # v37 FIX: embed the DB2 link too — same invite-link logic as DB
            link2 = await _db_invite_link(client, t["db2_id"])
            db_txt += " → DB2 " + await _chat_md(client, t["db2_id"], invite=link2)
        flag = " ⏸" if t.get("paused") else ""
        lines.append(f"  {i+1}. {t_md} → {db_txt} — resume at msg {prog}{flag}")
    return "\n".join(lines)


async def _target_link(client, tid, last_post):
    """v38: ALWAYS give a target a tappable link. Preferred: an invite link
    minted by the userbot (it is admin in the target channels) and cached in
    Mongo — works even before the first post is scraped. Fallback: the
    t.me/c/<id>/<last_post> trick once scraping has started."""
    inv = await _db_invite_link(client, tid)
    return inv or _c_link(tid, last_post)


async def _build_board_rows(client):
    """v37: gather the per-target data the rich charge-sheet board needs.
    Resolves titles/links once; targets link to their last scraped post (the
    t.me/c/… trick works for private channels), DB/DB2 use cached invite links."""
    targets = await DB.get_targets()
    rows = []
    for i, t in enumerate(targets):
        last = await DB.get_last_post(t["id"])
        rows.append({
            "n": i + 1,
            "id": t["id"],
            "title": await _chat_title(client, t["id"]) or str(t["id"]),
            "t_link": await _target_link(client, t["id"], last),
            "db_title": (await _chat_title(client, t["db_id"])
                         if t.get("db_id") else None),
            "db_link": (await _db_invite_link(client, t["db_id"])
                        if t.get("db_id") else None),
            "db2_title": (await _chat_title(client, t["db2_id"])
                          if t.get("db2_id") else None),
            "db2_link": (await _db_invite_link(client, t["db2_id"])
                         if t.get("db2_id") else None),
            "resume": await DB.get_progress(t["id"]),
            "paused": bool(t.get("paused")),
        })
    return rows


async def _add_target_with_db(scrape_client, ev, tid, dbid, db2=None):
    try:
        ent_t = await scrape_client.get_entity(tid)
    except Exception as e:
        await ev.reply(f"⚠️ Can't access that target with the userbot account: {e}")
        return
    if not _entity_ok(ent_t, "channel"):
        await ev.reply(_not_a_channel_msg(tid, "channel"))
        return
    try:
        ent_d = await scrape_client.get_entity(dbid)
    except Exception as e:
        await ev.reply(f"⚠️ Can't access that DB channel with the userbot account: {e}")
        return
    if not _entity_ok(ent_d, "channel"):
        await ev.reply(_not_a_channel_msg(dbid, "channel"))
        return
    await DB.add_target(tid, dbid)
    if db2 is not None:
        try:
            ent2 = await scrape_client.get_entity(db2)
            if not _entity_ok(ent2, "channel"):
                await ev.reply(_not_a_channel_msg(db2, "channel"))
                return
        except Exception as e:
            await ev.reply(f"⚠️ Can't access that DB2 channel: {e}")
            return
        await DB.set_target_db2(tid, db2)
    await ev.reply(f"✅ Target saved:\n  {tid} → DB {dbid}"
                   + (f" → DB2 {db2} (bot clean-mirror ON — make sure the BOT is admin in DB and DB2)"
                      if db2 is not None else "")
                   + "\n\n/targets to view all, /start to scrape.")


def register(scrape_client):
    # ---------- info ----------
    @bot.on(events.NewMessage(pattern=r"^/help$"))
    async def help_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        d = dict(_CMDS)
        def L(name):
            return f"/{name} — {d.get(name, '')}"
        sections = [
            ("ℹ️ INFO", ["help", "ping"]),
            ("👑 ADMINS", ["addadmin", "removeadmin"]),
            ("🎯 SETUP (targets · DB · bypass)",
             ["target", "targets", "deltarget", "setdb", "adddb", "bypass", "altbypass"]),
            ("🧼 DB2 CLEAN MIRROR (bot)", ["setdb2", "avoidtext", "removeavoid"]),
            ("🔗 CHECKDM PIPELINE (userbot)", ["checkdm"]),
            ("🔘 LINK-BOT BUTTONS", ["linkbutton", "removelinkbutton"]),
            ("▶️ SCRAPING", ["start", "pause", "resume", "stop", "skip", "cancel"]),
            ("📊 MONITOR", ["status", "current", "progress", "lastpost"]),
            ("🧭 PROGRESS CONTROL", ["goto", "reset"]),
            ("✏️ BULK TEXT EDIT (userbot)", ["replace", "deletetext"]),
            ("🧹 MASS DELETE (userbot)", ["massdlt", "massdlt_status", "massdlt_stop"]),
            ("📨 FORWARD / COPY (userbot)",
             ["forward", "forward_status", "forward_stop", "forward_resume"]),
            ("🤖 ADD BOTS (userbot)", ["add"]),
        ]
        lines = ["📖 COMMANDS"]
        shown = set()
        for head, names in sections:
            lines.append(f"\n{head}")
            for n in names:
                if n in d:
                    lines.append(L(n))
                    shown.add(n)
        rest = [c for c, _ in _CMDS if c not in shown]
        if rest:
            lines.append("\nOTHER")
            lines += [L(n) for n in rest]
        lines.append("\nTips: /target walks you through channel + its DB channel. "
                     "/goto accepts a message link (auto-picks the right target). "
                     "Bare /pause pauses EVERYTHING (also /pause all); /pause 2 pauses "
                     "ONLY target 2 (see /targets for numbers). Bare /resume resumes "
                     "everything; /resume 2 resumes one target. "
                     "When LINK_BOT renames its button: /linkbutton <new text> — "
                     "active instantly, no restart. "
                     "Caught-up channels re-scan for new posts every 30s.")
        await ev.reply("\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"^/ping$"))
    async def ping_cmd(ev):
        if await _admin(ev.sender_id):
            await ev.reply(f"🏓 Pong — control bot + scraper alive.\n"
                           f"📍 Stage: {state.stage} | Running: {state.running} | Paused: {state.paused}")

    # ---------- bulk channel text editing (userbot-powered) ----------
    @bot.on(events.NewMessage(pattern=r"^/replace(?:\s+([\s\S]+))?$"))
    async def replace_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        try:
            parts = shlex.split(arg)
        except ValueError:
            parts = []
        if len(parts) == 2:
            # DB2 form — the BOT edits its own clean-mirror posts (no userbot
            # flood hassle): /replace "old" "new"
            old, new = parts
            await _db2_bulk_edit(ev, old, new)
            return
        if len(parts) != 3:
            await ev.reply('Usage: /replace <channel id> "target text" "replacement text"\n'
                           'The USERBOT edits that channel (needs edit rights).\n'
                           'Or: /replace "old" "new" — the BOT edits every DB2 mirror channel.\n'
                           'Quotes are needed when texts contain spaces.')
            return
        channel, old, new = parts
        await _bulk_edit(scrape_client, ev, _parse_chat_id(channel), old, new)

    @bot.on(events.NewMessage(pattern=r"^/deletetext(?:\s+([\s\S]+))?$"))
    async def deletetext_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        try:
            parts = shlex.split(arg)
        except ValueError:
            parts = []
        if len(parts) != 2:
            await ev.reply('Usage: /deletetext <channel id> "text to delete"\n'
                           'Quotes are needed when the text contains spaces. '
                           'The USERBOT edits the posts (must have edit rights).')
            return
        channel, old = parts
        await _bulk_edit(scrape_client, ev, _parse_chat_id(channel), old, "")

    # ---------- MTProto bulk jobs: mass delete / forward / add-bot-admin ----------
    @bot.on(events.NewMessage(pattern=r"^/massdlt(?:\s+([\s\S]+))?$"))
    async def massdlt_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        try:
            parts = shlex.split(arg)
        except ValueError:
            parts = []
        if len(parts) != 3:
            await ev.reply("Usage: /massdlt <chat_id> <start_link> <end_link>\n"
                           "Deletes every message between the two links (inclusive). "
                           "Chunked + paced (Telegram-safe) so a 2000-message range "
                           "is deleted piece-by-piece, never flooded.")
            return
        chat, s, e = parts
        await MTM.massdlt_start(scrape_client, ev, _parse_chat_id(chat), s, e)

    @bot.on(events.NewMessage(pattern=r"^/massdlt_status$"))
    async def massdlt_status_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        txt = MTM.massdlt_job.progress_text()
        await ev.reply(txt or "ℹ️ No mass-delete has run yet.")

    @bot.on(events.NewMessage(pattern=r"^/massdlt_stop$"))
    async def massdlt_stop_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        if MTM.massdlt_job.status == "running":
            MTM.massdlt_job.stop = True
            MTM.massdlt_job.status = "stopping"
            await ev.reply("🛑 Stopping mass-delete — it stops after the current chunk. "
                           "/massdlt_status to confirm.")
        else:
            await ev.reply("ℹ️ No mass-delete is running.")

    @bot.on(events.NewMessage(pattern=r"^/forward(?:\s+([\s\S]+))?$"))
    async def forward_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        try:
            parts = shlex.split(arg)
        except ValueError:
            parts = []
        if len(parts) != 4:
            await ev.reply("Usage: /forward <target_channel> <source_channel> <start_link> <end_link>\n"
                           "Copies every message in the range to the target channel — "
                           "by reference, NO 'Forwarded from' tag, paced to avoid floods. "
                           "Stops/crashes are resumable with /forward_resume.")
            return
        tgt, src, s, e = parts
        await MTM.forward_start(scrape_client, ev, _parse_chat_id(tgt),
                                _parse_chat_id(src), s, e)

    @bot.on(events.NewMessage(pattern=r"^/forward_status$"))
    async def forward_status_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        txt = MTM.forward_job.progress_text()
        if txt is None:
            saved = await DB.get_config("fwd_job")
            if saved and saved.get("ids") and saved.get("pos", 0) < len(saved["ids"]):
                txt = (f"⏸ forward — stopped/interrupted\n"
                       f"   {saved.get('source')} → {saved.get('target')}\n"
                       f"   {saved.get('pos', 0)}/{len(saved['ids'])} done — "
                       f"/forward_resume to continue.")
            else:
                txt = "ℹ️ No forward has run yet."
        await ev.reply(txt)

    @bot.on(events.NewMessage(pattern=r"^/forward_stop$"))
    async def forward_stop_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        if MTM.forward_job.status == "running":
            MTM.forward_job.stop = True
            MTM.forward_job.status = "stopping"
            await ev.reply("🛑 Stopping forward — cursor saved in MongoDB; "
                           "/forward_resume continues from the exact message.")
        else:
            await ev.reply("ℹ️ No forward is running.")

    @bot.on(events.NewMessage(pattern=r"^/forward_resume$"))
    async def forward_resume_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        await MTM.forward_resume(scrape_client, ev)

    @bot.on(events.NewMessage(pattern=r"^/add(?:\s+([\s\S]+))?$"))
    async def add_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        try:
            parts = shlex.split(arg)
        except ValueError:
            parts = []
        if len(parts) < 2:
            await ev.reply("Usage: /add <channel id> @bot1 [@bot2 @bot3 …]\n"
                           "Adds each bot to the channel as ADMIN with ALL permissions. "
                           "The USERBOT does it — it must already be an admin there "
                           "with add-admins permission.")
            return
        channel, bots = parts[0], parts[1:]
        await MTM.add_bots(scrape_client, ev, _parse_chat_id(channel), bots)

    # ---------- DB2 clean mirror (BOT copies DB -> DB2, credits stripped) ----------
    _URL_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/\S+|https?://\S+")
    _MENTION_RE = re.compile(r"@[A-Za-z0-9_]+")

    def _clean_caption(text, avoids):
        """Strip custom avoid-strings, all URLs (t.me + http) and @mentions from
        a caption, then tidy leftover whitespace. Sent as plain text, so any
        embedded-link formatting in the original dies with the entities."""
        t = text or ""
        for a in avoids or []:
            t = t.replace(a, "")
        t = _URL_RE.sub("", t)
        t = _MENTION_RE.sub("", t)
        t = re.sub(r"[ \t]+\n", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    _DB2_CACHE = {"ts": 0.0, "map": {}}

    async def _db2_map():
        """db_channel_id -> target dict, for every target with a DB2 set.
        Cached 30s so normal chats cost no Mongo query."""
        if time.time() - _DB2_CACHE["ts"] > 30:
            m = {}
            for t in await DB.get_targets():
                if t.get("db2_id") and t.get("db_id"):
                    m[t["db_id"]] = t
            _DB2_CACHE["ts"] = time.time()
            _DB2_CACHE["map"] = m
        return _DB2_CACHE["map"]

    async def _copy_to_db2(m, db2, avoids):
        """Copy ONE DB message into DB2 as a fresh bot post (no forward tag),
        caption cleaned. FloodWait is slept through in place."""
        cap = _clean_caption(m.message or "", avoids)
        spoiler = bool(getattr(getattr(m, "media", None), "spoiler", False))
        for _ in range(3):
            try:
                if getattr(m, "media", None) is not None:
                    await bot.send_file(db2, m.media, caption=cap,
                                        buttons=m.buttons, spoiler=spoiler)
                elif cap:
                    await bot.send_message(db2, cap)
                return True
            except FloodWaitError as fe:
                await asyncio.sleep(min(getattr(fe, "seconds", 60), BULK_MAX_FLOOD) + 5)
            except Exception as e:
                log_mirror.warning("DB2 copy failed: %s", e)
                return False
        return False

    _DB2_LOCKS = {}  # db chat_id -> asyncio.Lock (order preservation)

    @bot.on(events.NewMessage())
    async def db2_mirror(ev):
        """Auto-mirror: userbot posts to DB -> bot re-posts to DB2 cleaned.
        Requires the BOT to be admin in BOTH DB (to receive channel posts) and
        DB2 (to post). Serialized per DB channel: Telethon dispatches channel
        events concurrently, so without this lock a slow/flooded cover copy
        could finish AFTER the next message's copy — DB2 got videos before the
        cover. The lock guarantees DB2 order == DB order (cover first)."""
        t = (await _db2_map()).get(ev.chat_id)
        if not t:
            return
        lock = _DB2_LOCKS.setdefault(ev.chat_id, asyncio.Lock())
        async with lock:
            await _copy_to_db2(ev.message, t["db2_id"], t.get("avoid") or [])

    async def _db2_bulk_edit(ev, old, new):
        """BOT edits every message containing `old` in every DB2 channel —
        these are the bot's OWN posts, so editing is allowed. Paced + flood-safe."""
        targets = [t for t in await DB.get_targets() if t.get("db2_id")]
        if not targets:
            await ev.reply("No DB2 channels set — add one with /setdb2 <n> <id>.")
            return
        status = await ev.reply(f"🔍 Scanning {len(targets)} DB2 channel(s) for \"{old}\"…")
        edited = failed = floods = 0
        for t in targets:
            db2 = t["db2_id"]
            matches = []
            try:
                async for m in bot.iter_messages(db2):
                    if old in (m.message or ""):
                        matches.append(m)
            except Exception as e:
                await ev.reply(f"⚠️ Can't read DB2 {db2} (is the bot an admin there?): {e}")
                continue
            if not matches:
                continue
            await ev.reply(f"DB2 {db2}: editing {len(matches)} message(s)…")
            for m in matches:
                new_txt = (m.message or "").replace(old, new).strip()
                while True:
                    try:
                        await bot.edit_message(db2, m.id, text=new_txt, buttons=m.buttons)
                        edited += 1
                        break
                    except FloodWaitError as fe:
                        secs = getattr(fe, "seconds", 60)
                        if secs <= BULK_MAX_FLOOD:
                            floods += 1
                            await asyncio.sleep(secs + 5)
                            continue
                        failed += 1
                        break
                    except Exception:
                        failed += 1
                        break
                if edited and edited % BULK_PROGRESS_EVERY == 0:
                    try:
                        await status.edit(f"⏳ {edited} edited, {failed} failed…")
                    except Exception:
                        pass
                await asyncio.sleep(BULK_EDIT_DELAY + random.uniform(0, 1.5))
        await status.edit(f"✅ DB2 edit done — {edited} message(s) updated"
                          + (f", {failed} failed" if failed else "")
                          + (f", {floods} flood wait(s) slept" if floods else ""))

    @bot.on(events.NewMessage(pattern=r"^/setdb2(?:\s+([\s\S]+))?$"))
    async def setdb2_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        try:
            parts = shlex.split(arg)
        except ValueError:
            parts = []
        targets = await DB.get_targets()
        if len(parts) != 2:
            cur = [f"  {i+1}. {t['id']} → DB2 {t.get('db2_id') or '(off)'}"
                   for i, t in enumerate(targets)]
            await ev.reply("Usage: /setdb2 <target #> <db2 channel id | off>\n"
                           "The BOT re-posts everything from that target's DB into DB2 "
                           "with credits/links stripped. Bot must be admin in both.\n"
                           + ("\n".join(cur) if cur else "No targets yet."))
            return
        n_raw, db2_raw = parts
        t = None
        if n_raw.isdigit() and 1 <= int(n_raw) <= len(targets):
            t = targets[int(n_raw) - 1]
        else:
            for cand in targets:
                if str(cand["id"]) == n_raw:
                    t = cand
                    break
        if not t:
            await ev.reply("⚠️ Unknown target — see /targets for numbers.")
            return
        if db2_raw.lower() in ("off", "none", "0", "-"):
            await DB.set_target_db2(t["id"], None)
            await ev.reply(f"🧼 DB2 mirror OFF for target {t['id']}.")
            return
        db2 = _parse_chat_id(db2_raw)
        try:
            ent = await scrape_client.get_entity(db2)
        except Exception as e:
            await ev.reply(f"⚠️ Can't access that DB2 channel: {e}")
            return
        if not _entity_ok(ent, "channel"):
            await ev.reply(_not_a_channel_msg(db2, "channel"))
            return
        await DB.set_target_db2(t["id"], db2)
        await ev.reply(f"✅ Target {t['id']} now mirrors DB → DB2 {db2} (credits stripped).\n"
                       "Make sure the BOT is admin in the DB channel AND in DB2.\n"
                       "Add credit strings to strip with /avoidtext.")

    @bot.on(events.NewMessage(pattern=r"^/avoidtext(?:\s+([\s\S]+))?$"))
    async def avoidtext_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        try:
            parts = shlex.split(arg)
        except ValueError:
            parts = []
        targets = await DB.get_targets()
        if not parts:
            lines = ["🧼 DB2 avoid-strings (stripped from mirrored captions, "
                     "on top of auto-stripped @mentions and links):"]
            for i, t in enumerate(targets):
                av = t.get("avoid") or []
                lines.append(f"  {i+1}. {t['id']}: " + (", ".join(f'\"{a}\"' for a in av) if av else "(none)"))
            lines.append("\n/avoidtext <target #> \"text\" to add — /removeavoid <target #> <#> to remove.")
            await ev.reply("\n".join(lines))
            return
        if len(parts) == 1:
            if not (parts[0].isdigit() and 1 <= int(parts[0]) <= len(targets)):
                await ev.reply("Usage: /avoidtext <target #> \"text to strip\" — quotes when it has spaces.")
                return
            t = targets[int(parts[0]) - 1]
            av = t.get("avoid") or []
            await ev.reply(f"🧼 Avoid-strings for target {int(parts[0])} ({t['id']}):\n"
                           + ("\n".join(f"  {j+1}. \"{a}\"" for j, a in enumerate(av)) if av else "  (none)")
                           + "\n\n/avoidtext " + parts[0] + " \"text\" to add, /removeavoid " + parts[0] + " <#> to remove.")
            return
        # v30: multi-word avoid strings — after the target number, EVERYTHING
        # (quoted or not) is the string to strip, so both of these work:
        #   /avoidtext 1 "how are you"   and   /avoidtext 1 how are you
        n_raw, text = parts[0], " ".join(parts[1:]).strip()
        if not (n_raw.isdigit() and 1 <= int(n_raw) <= len(targets)):
            await ev.reply("⚠️ Unknown target number — see /targets.")
            return
        t = targets[int(n_raw) - 1]
        av, added = await DB.add_avoid(t["id"], text)
        if av is None:
            await ev.reply("⚠️ Target not found.")
        elif added:
            await ev.reply(f"✅ Target {n_raw}: will strip \"{text}\" from DB2 captions "
                           f"({len(av)} avoid-string(s) now).")
        else:
            await ev.reply("ℹ️ That string is already on the avoid list.")

    @bot.on(events.NewMessage(pattern=r"^/removeavoid(?:\s+(\d+)\s+(\d+))?$"))
    async def removeavoid_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        n, idx = ev.pattern_match.group(1), ev.pattern_match.group(2)
        targets = await DB.get_targets()
        if not n or not idx or not (1 <= int(n) <= len(targets)):
            await ev.reply("Usage: /removeavoid <target #> <string #> — numbers from /avoidtext.")
            return
        t = targets[int(n) - 1]
        res = await DB.remove_avoid(t["id"], int(idx))
        if res is None:
            await ev.reply("⚠️ No such string number — see /avoidtext.")
        else:
            _, removed = res
            await ev.reply(f"🗑 Target {n}: stopped stripping \"{removed}\".")

    # ---------- /checkdm: userbot DM pipeline (@richmining -> auto admin) ----------
    @bot.on(events.NewMessage(pattern=r"^/checkdm(?:\s+(\S+))?$"))
    async def checkdm_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip().lower()
        if arg in ("on", "off"):
            await DB.set_config("checkdm_enabled", arg == "on")
            if arg == "on":
                await ev.reply("🔗 checkdm ON — the userbot now watches its DM with "
                               "@richmining. Send a channel invite link (public "
                               "t.me/name or private t.me/+hash) and it will: join → "
                               "wait until it's promoted to admin → add @lifesimplerbot "
                               "as admin with the same rights the userbot has → leave → "
                               "reply DONE ✅. Then it's ready for the next link.")
            else:
                await ev.reply("🔗 checkdm OFF — invite links from @richmining are ignored.")
            return
        cur = bool(await DB.get_config("checkdm_enabled"))
        await ev.reply(f"🔗 checkdm is {'ON ✅' if cur else 'OFF ❌'}.\n"
                       "Usage: /checkdm on  |  /checkdm off")

    # ---------- extra admins (owner-only management, Mongo-backed) ----------
    def _owner(ev):
        return ADMIN_USER_ID == 0 or ev.sender_id == ADMIN_USER_ID

    @bot.on(events.NewMessage(pattern=r"^/addadmin(?:\s+(\S+))?$"))
    async def addadmin_cmd(ev):
        if not _owner(ev):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        if not arg:
            admins = await DB.get_admins()
            lines = [f"👑 Owner: `{ADMIN_USER_ID}`", "🛡 Admins (full bot access):"]
            lines += ([f"  {i+1}. `{a}`" for i, a in enumerate(admins)]
                      if admins else ["  (none yet)"])
            lines.append("\n/addadmin <user id> to add one — they can use EVERY "
                         "command like the owner. Get an id from @userinfobot.")
            await ev.reply("\n".join(lines))
            return
        try:
            uid = int(arg)
        except ValueError:
            await ev.reply("⚠️ /addadmin needs the NUMERIC Telegram user id "
                           "(e.g. /addadmin 123456789) — @userinfobot gives it.")
            return
        if ADMIN_USER_ID and uid == ADMIN_USER_ID:
            await ev.reply("ℹ️ That's the owner already.")
            return
        admins = await DB.add_admin(uid)
        await ev.reply(f"✅ `{uid}` is now an admin — full bot access granted. "
                       f"({len(admins)} admin(s) total)")

    @bot.on(events.NewMessage(pattern=r"^/removeadmin(?:\s+(\S+))?$"))
    async def removeadmin_cmd(ev):
        if not _owner(ev):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        if not arg:
            await ev.reply("Usage: /removeadmin <user id> — bare /addadmin lists current admins.")
            return
        try:
            uid = int(arg)
        except ValueError:
            await ev.reply("⚠️ numeric user id only.")
            return
        admins, removed = await DB.remove_admin(uid)
        await ev.reply(f"🗑 `{uid}` removed — they can no longer use the bot."
                       if removed else "ℹ️ That id isn't in the admin list.")

    # ---------- LINK_BOT button labels (Mongo-backed, no restart) ----------
    @bot.on(events.NewMessage(pattern=r"^/linkbutton(?:\s+(.+))?$"))
    async def linkbutton_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        if arg:
            # strip surrounding quotes if the owner wrapped the label
            if len(arg) > 1 and arg[0] in '"\'' and arg[-1] == arg[0]:
                arg = arg[1:-1].strip()
            if not arg:
                await ev.reply("⚠️ Empty label. Usage: /linkbutton <button text>")
                return
            buttons, added = await DB.add_link_button(arg)
            if added:
                await ev.reply(f"✅ Button label added: {arg}\n"
                               f"LINK_BOT link button now matches: "
                               f"{BTN_SHORT_LINK} (built-in) + {len(buttons)} custom label(s). "
                               f"Active immediately — /linkbutton to list, /removelinkbutton <n> to remove.")
            else:
                await ev.reply(f"⚠️ '{arg}' is already in the list (see /linkbutton).")
            return
        buttons = await DB.get_link_buttons()
        lines = [f"🔗 LINK_BOT button labels (built-in: '{BTN_SHORT_LINK}' — always active):"]
        if buttons:
            lines += [f"  {i+1}. {b}" for i, b in enumerate(buttons)]
            lines.append("\n/linkbutton <text> to add, /removelinkbutton <n> to remove.")
        else:
            lines.append("  (no custom labels yet)")
            lines.append("\nIf LINK_BOT renamed its button: /linkbutton <exact new text> — "
                         "the userbot matches it on the next post, no restart needed.")
        await ev.reply("\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"^/removelinkbutton(?:\s+(\d+))?$"))
    async def removelinkbutton_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        n = ev.pattern_match.group(1)
        if not n:
            await ev.reply("Usage: /removelinkbutton <number> — see /linkbutton for the numbered list.")
            return
        r = await DB.remove_link_button(int(n))
        if r is None:
            buttons = await DB.get_link_buttons()
            await ev.reply(f"⚠️ Number must be 1-{len(buttons)} (see /linkbutton).")
            return
        buttons, removed = r
        await ev.reply(f"🗑 Removed label {n}: {removed}\n"
                       f"{len(buttons)} custom label(s) left (built-in '{BTN_SHORT_LINK}' is always active).")

    # ---------- wizards (all accept inline args too) ----------
    @bot.on(events.NewMessage(pattern=r"^/target(?:\s+(.+))?$"))
    async def target_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        if arg:
            _pending[ev.sender_id] = ("target_db", _parse_chat_id(arg))
            await ev.reply(f"Target: **{_parse_chat_id(arg)}**\nNow send the DB channel id for THIS target "
                           f"(where its videos+srt go).\nCancel: /cancel")
            return
        _pending[ev.sender_id] = ("target_id", None)
        await ev.reply("Send me the TARGET channel id (numeric like -100… or @username).\nCancel: /cancel")

    @bot.on(events.NewMessage(pattern=r"^/(bypass|adddb|altbypass)(?:\s+(.+))?$"))
    async def simple_wizard(ev):
        if not await _admin(ev.sender_id):
            return
        cmd = ev.pattern_match.group(1)
        arg = (ev.pattern_match.group(2) or "").strip()
        field = {"bypass": "bypass_id", "adddb": "db_id",
                 "altbypass": "alt_bypass_id"}[cmd]
        if arg:
            await _save_simple(ev, field, _parse_chat_id(arg))
            return
        _pending[ev.sender_id] = (field, None)
        if field == "bypass_id":
            await ev.reply(
                "Send me the bypass endpoint — either a GROUP id (like -100…) "
                "or a BOT @username (e.g. @dex_fekkyeww_bot).\n"
                "Bot endpoints reply in DM with the bypassed link in text — no "
                "'Open link' button needed.\nCancel: /cancel")
        elif field == "alt_bypass_id":
            await ev.reply(
                "Send me the ALT bypass endpoint (GROUP id or BOT @username).\n"
                "Used ONLY when the primary /bypass doesn't return a link — "
                "the second session (STRING_SESSION2) talks to it when it's the "
                "active account. Both fail = you get a DM alert with the post link.\n"
                "Cancel: /cancel")
        else:
            await ev.reply("Send me the fallback DB channel id (numeric like -100… or @username).\nCancel: /cancel")

    async def _save_simple(ev, field, v):
        try:
            ent = await scrape_client.get_entity(v)
        except Exception as e:
            await ev.reply(f"⚠️ Can't access that chat with the userbot account: {e}")
            return
        if field == "db_id" and not _entity_ok(ent, "channel"):
            await ev.reply(_not_a_channel_msg(v, "channel"))
            return
        if field in ("bypass_id", "alt_bypass_id"):
            # bypass endpoints can be a GROUP (Channel/Chat) OR a BOT (User with bot=True)
            is_group = _entity_ok(ent, "group")
            is_bot = isinstance(ent, User) and getattr(ent, "bot", False)
            if not (is_group or is_bot):
                await ev.reply(
                    f"⚠️ {v} isn't a group and isn't a bot — bypass must be one of those.\n"
                    "For a bot, send its @username (e.g. @dex_fekkyeww_bot). "
                    "For a group, paste any message link from the group "
                    "(https://t.me/c/1234567890/12) or its -100… id.")
                return
            kind = "bot" if is_bot else "group"
            label = "bypass" if field == "bypass_id" else "ALT bypass"
            await DB.set_config(field, v)
            await ev.reply(f"✅ Saved {label} = {v} ({kind}). "
                           + ("No 'Open link' button needed — the bypassed t.me link is read from the reply text."
                              if is_bot else
                              "The tagger's 'Open link' button reply is expected.")
                           + ("" if field == "bypass_id" else
                              " Used automatically if the primary /bypass fails."))
            return
        await DB.set_config(field, v)
        await ev.reply(f"✅ Saved {field} = {v}")

    @bot.on(events.NewMessage(pattern=r"^/deltarget(?:\s+(.+))?$"))
    async def deltarget_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        targets = await DB.get_targets()
        if not targets:
            await ev.reply("No target channels set. Add one with /target")
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        if arg:
            await _do_remove(ev, arg)
            return
        _pending[ev.sender_id] = ("deltarget", None)
        listing = await _list_targets_text(scrape_client)
        await ev.reply(f"{listing}\n\nSend the id (or list number) to REMOVE.\n"
                       "Progress is kept — re-adding later resumes where it left off.\nCancel: /cancel")

    async def _do_remove(ev, raw):
        targets = await DB.get_targets()
        if raw.isdigit() and 1 <= int(raw) <= len(targets):
            v = targets[int(raw) - 1]["id"]
        else:
            v = _parse_id(raw)
        if v not in [t["id"] for t in targets]:
            await ev.reply(f"⚠️ {v} is not in your targets list.")
            return
        remaining = await DB.remove_target(v)
        await ev.reply(f"🗑 Removed {v} (its progress is kept).\n"
                       f"Targets left: {[t['id'] for t in remaining] or 'none'}")

    @bot.on(events.NewMessage(pattern=r"^/setdb(?:\s+(.+))?$"))
    async def setdb_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        targets = await DB.get_targets()
        if not targets:
            await ev.reply("No target channels. Add one with /target")
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        if arg:
            await _do_setdb(ev, arg)
            return
        _pending[ev.sender_id] = ("setdb", None)
        listing = await _list_targets_text(scrape_client)
        await ev.reply(f"{listing}\n\nSend: <number> <db_id>  (e.g.  2 -100999888777)\nCancel: /cancel")

    async def _do_setdb(ev, arg):
        parts = arg.split()
        targets = await DB.get_targets()
        if len(parts) != 2 or not parts[0].isdigit() or not (1 <= int(parts[0]) <= len(targets)):
            await ev.reply("⚠️ Format: <target number> <db_id> — e.g.  2 -100999888777")
            return
        t = targets[int(parts[0]) - 1]
        dbid = _parse_chat_id(parts[1])
        try:
            ent = await scrape_client.get_entity(dbid)
        except Exception as e:
            await ev.reply(f"⚠️ Can't access that DB channel with the userbot account: {e}")
            return
        if not _entity_ok(ent, "channel"):
            await ev.reply(_not_a_channel_msg(dbid, "channel"))
            return
        await DB.set_target_db(t["id"], dbid)
        await ev.reply(f"✅ Target {t['id']} now posts to DB {dbid}")

    @bot.on(events.NewMessage(pattern=r"^/cancel$"))
    async def cancel(ev):
        _pending.pop(ev.sender_id, None)
        await ev.reply("Cancelled.")

    @bot.on(events.NewMessage())
    async def wizard_answer(ev):
        item = _pending.get(ev.sender_id)
        if not item or not await _admin(ev.sender_id) or ev.raw_text.startswith("/"):
            return  # a new /command cancels the pending wizard instead of being eaten
        kind, extra = item
        if kind == "target_id":
            tid = _parse_chat_id(ev.raw_text)
            try:
                ent = await scrape_client.get_entity(tid)
            except Exception as e:
                await ev.reply(f"⚠️ Can't access that channel with the userbot account: {e}")
                return
            if not _entity_ok(ent, "channel"):
                await ev.reply(_not_a_channel_msg(tid, "channel"))
                return
            _pending[ev.sender_id] = ("target_db", tid)
            await ev.reply(f"Target: **{tid}**\nNow send the DB channel id for THIS target "
                           f"(where its videos+srt go).\nCancel: /cancel")
            return
        if kind == "target_db":
            dbid = _parse_chat_id(ev.raw_text)
            _pending[ev.sender_id] = ("target_db2", (extra, dbid))
            await ev.reply(f"DB: **{dbid}**\nOptional: send the DB2 (clean mirror) channel id for "
                           "this target — the BOT will re-post everything from DB into DB2 "
                           "with credits/links stripped. Send `skip` to leave it off.\n"
                           "Cancel: /cancel")
            return
        if kind == "target_db2":
            _pending.pop(ev.sender_id, None)
            tid, dbid = extra
            raw = ev.raw_text.strip().lower()
            db2 = None if raw in ("skip", "-", "none", "off", "0") else _parse_chat_id(ev.raw_text)
            await _add_target_with_db(scrape_client, ev, tid, dbid, db2)
            return
        if kind == "deltarget":
            _pending.pop(ev.sender_id, None)
            await _do_remove(ev, ev.raw_text.strip())
            return
        if kind == "setdb":
            _pending.pop(ev.sender_id, None)
            await _do_setdb(ev, ev.raw_text.strip())
            return
        # simple fields: bypass_id / db_id
        _pending.pop(ev.sender_id, None)
        await _save_simple(ev, kind, _parse_chat_id(ev.raw_text))

    # ---------- view ----------
    @bot.on(events.NewMessage(pattern=r"^/targets(_text)?$"))
    async def targets_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        plain = ev.pattern_match.group(1) == "_text"
        targets = await DB.get_targets()
        if not targets:
            await ev.reply("No target channels. Add one with /target")
            return
        if not plain:
            # v37: try the Bot API rich charge sheet (table + coloured buttons);
            # falls back to the markdown listing when the Bot API server
            # doesn't support rich messages yet.
            rows = await _build_board_rows(scrape_client)
            if await richboard.send_targets_board(ev.chat_id, rows):
                return
        listing = await _list_targets_text(scrape_client)
        await ev.reply(listing + "\n\n/setdb to change a DB, /deltarget to remove, "
                                 "/pause <n> //resume <n> to pause/resume one target.")

    @bot.on(events.CallbackQuery(pattern=rb"^tglp:"))
    async def board_cb(ev):
        """v37: charge-sheet board buttons (Pause/Resume toggle, Refresh)."""
        if not await _admin(ev.sender_id):
            await ev.answer("⛔ Admins only", alert=True)
            return
        action, n = richboard.parse_callback(ev.data.decode())
        # v38: boards expire after BOARD_TTL — late taps get a popup instead
        # of acting on a stale board (saves server resources too)
        if richboard.board_expired(ev.chat_id, getattr(ev, "message_id", None)):
            await ev.answer("This board has expired. Run /targets to open a fresh board.",
                            alert=True)
            return
        if action == "refresh":
            rows = await _build_board_rows(scrape_client)
            await richboard.refresh_board(ev.chat_id, rows)   # edits in place
            await ev.answer("🔄 Board refreshed")
            return
        if action == "toggle":
            targets = await DB.get_targets()
            if not (1 <= n <= len(targets)):
                await ev.answer("⚠️ That target no longer exists", alert=True)
                return
            t = targets[n - 1]
            if t.get("paused"):
                await DB.set_target_paused(t["id"], False)
                state.paused_ids.discard(t["id"])
                state.paused = False; state.user_paused = False
                state.abort = False; state.started = True
                state.reset_gen += 1; state._last_scan = None
                await ev.answer(f"▶️ Target {n} resumed")
            else:
                await DB.set_target_paused(t["id"], True)
                state.paused_ids.add(t["id"])
                state.reset_gen += 1; state._last_scan = None
                await ev.answer(f"⏸ Target {n} paused")
            rows = await _build_board_rows(scrape_client)
            await richboard.refresh_board(ev.chat_id, rows)   # edits in place
            return
        await ev.answer()

    @bot.on(events.NewMessage(pattern=r"^/lastpost(?:\s+(\d+))?$"))
    async def lastpost(ev):
        if not await _admin(ev.sender_id):
            return
        targets = await DB.get_targets()
        if not targets:
            await ev.reply("Set a target first: /target")
            return
        n = ev.pattern_match.group(1)
        idx = int(n) - 1 if n and n.isdigit() and 1 <= int(n) <= len(targets) else 0
        tid = targets[idx]["id"]
        from scraper import is_post, find_button
        from config import BTN_DOWNLOAD
        total = 0
        newest = None
        async for m0 in scrape_client.iter_messages(tid, limit=500):
            total += 1
            if newest is None and is_post(m0):
                newest = m0
        resume_at = await DB.get_progress(tid)
        if newest:
            m = newest
            btn = find_button(m, BTN_DOWNLOAD)
            cap = (m.message or "")[:200].replace("\n", " ")
            await ev.reply(f"📄 Target {idx+1}: {tid}\n• messages scanned: {total}"
                           + f"\n• newest post msg id: {m.id}"
                           + "\n• date: " + m.date.strftime("%Y-%m-%d %H:%M UTC")
                           + "\n• caption: " + cap
                           + "\n• Download button: " + ("yes — " + btn[2].text if btn else "NO")
                           + f"\n• current resume point: {resume_at}"
                           + "\n\nTip: /reset to scrape from post 1, /goto to start elsewhere.")
            return
        await ev.reply(f"Scanned {total} messages in {tid} — none look like posts "
                       "(media+caption+Download button).")

    # ---------- control ----------
    @bot.on(events.NewMessage(pattern=r"^/start$"))
    async def start_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        targets = await DB.get_targets()
        cfg = await DB.get_config()
        missing = []
        if not targets:
            missing.append("target (use /target)")
        if not cfg.get("bypass_id"):
            missing.append("bypass_id")
        if not cfg.get("db_id") and not all(t.get("db_id") for t in targets):
            missing.append("db (per-target via /setdb or fallback via /adddb)")
        if missing:
            await ev.reply("⚠️ Cannot start — missing: " + ", ".join(missing))
            return
        state.abort = False; state.paused = False; state.started = True
        await ev.reply("▶️ Scraper started. It scans each target from its saved point.\n"
                       "Check /progress anytime.")

    @bot.on(events.NewMessage(pattern=r"^/(pause|resume)(?:\s+(\S+))?$"))
    async def pause_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        action = ev.pattern_match.group(1)
        n = ev.pattern_match.group(2)
        if action == "pause":
            if n is None or n.lower() == "all":
                # v36 FIX: global pause is now persistent + visible (see
                # _pause_all_targets). Bare /pause or /pause all both trigger it.
                flipped, total = await _pause_all_targets()
                await ev.reply("⏸ Paused ALL targets"
                               + (f" ({flipped} newly paused)" if flipped else " (already paused)")
                               + ". Progress is saved in MongoDB — safe even if Render "
                               "crashes. /resume all to continue.")
                return
            if not n.isdigit():
                await ev.reply("Usage: /pause <target #> or /pause all — see /targets for numbers.")
                return
            targets = await DB.get_targets()
            if not (1 <= int(n) <= len(targets)):
                await ev.reply((await _list_targets_text(scrape_client) or "No targets set.") +
                               f"\n\n⚠️ Target number must be 1-{len(targets)} (see /targets).")
                return
            t = targets[int(n) - 1]
            if t.get("paused"):
                await ev.reply(f"⏸ Target {n} ({t['id']}) is already paused.")
                return
            await DB.set_target_paused(t["id"], True)
            state.paused_ids.add(t["id"])          # loop sees it within seconds
            state.reset_gen += 1; state._last_scan = None  # drop the current pass
            await ev.reply(f"⏸ Target {n} ({t['id']}) paused — other targets keep scraping.\n"
                           f"/resume {n} to resume this one.")
            return
        # ---- /resume ----
        if n is not None and n.lower() != "all" and not n.isdigit():
            await ev.reply("Usage: /resume <target #>, /resume all, or bare /resume (everything).")
            return
        if n is None or n.lower() == "all":
            # v35: bare /resume resumes EVERYTHING (same as /resume all):
            # global flag + manual-pause flag + all per-target flags
            state.paused = False; state.user_paused = False
            state.abort = False; state.started = True
            targets = await DB.get_targets()
            unpaused = 0
            for t in targets:
                if t.get("paused"):
                    await DB.set_target_paused(t["id"], False)
                    unpaused += 1
            state.paused_ids.clear()
            state.reset_gen += 1; state._last_scan = None
            msg = "▶️ Resumed from saved progress."
            if unpaused:
                msg += f" ({unpaused} individually-paused target(s) resumed too.)"
            await ev.reply(msg)
            return
        if not n.isdigit():
            await ev.reply("Usage: /resume <target #> or /resume all — see /targets for numbers.")
            return
        targets = await DB.get_targets()
        if not (1 <= int(n) <= len(targets)):
            await ev.reply((await _list_targets_text(scrape_client) or "No targets set.") +
                           f"\n\n⚠️ Target number must be 1-{len(targets)} (see /targets).")
            return
        t = targets[int(n) - 1]
        await DB.set_target_paused(t["id"], False)
        state.paused_ids.discard(t["id"])
        state.paused = False; state.user_paused = False
        state.abort = False; state.started = True
        state.reset_gen += 1; state._last_scan = None
        rp = await DB.get_progress(t["id"])
        await ev.reply(f"▶️ Target {n} ({t['id']}) resumed — continues from message id {rp}.")

    @bot.on(events.NewMessage(pattern=r"^/(status|current)$"))
    async def status_cmd(ev):
        if await _admin(ev.sender_id):
            cfg = await DB.get_config()
            listing = await _list_targets_text(scrape_client) or "  (none)"
            ind = sorted(str(i + 1) for i, t in enumerate(await DB.get_targets()) if t.get("paused"))
            await ev.reply(f"🤖 Running: {state.running} | Paused: {state.paused}"
                           + (f" | Individually paused targets: {', '.join(ind)}" if ind else "") + "\n"
                           f"📍 Stage: {state.stage}\n📄 Current post: {state.current_post}\n"
                           f"{listing}\n🔁 Bypass: {cfg.get('bypass_id')}\n"
                           f"🗄 Fallback DB: {cfg.get('db_id')}")

    @bot.on(events.NewMessage(pattern=r"^/progress$"))
    async def progress_cmd(ev):
        if await _admin(ev.sender_id):
            from bot import fmt_progress
            await ev.reply(await fmt_progress(scrape_client))

    @bot.on(events.NewMessage(pattern=r"^/skip$"))
    async def skip_cmd(ev):
        if await _admin(ev.sender_id):
            state.abort = True
            await ev.reply("⏭ Skipping current post…")

    @bot.on(events.NewMessage(pattern=r"^/stop$"))
    async def stop_cmd(ev):
        if await _admin(ev.sender_id):
            state.abort = True; state.running = False; state.started = False
            await ev.reply("🛑 Stopped. Progress saved — /resume or /start continues from the same post.")

    # ---------- per-target reset & goto ----------
    @bot.on(events.NewMessage(pattern=r"^/reset(?:\s+(\d+))?$"))
    async def reset_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        targets = await DB.get_targets()
        if not targets:
            await ev.reply("Set a target first: /target")
            return
        n = ev.pattern_match.group(1)
        if n and n.isdigit() and 1 <= int(n) <= len(targets):
            idx = int(n) - 1
        elif len(targets) == 1:
            idx = 0
        else:
            await ev.reply((await _list_targets_text()) +
                           "\n\nWhich target? Send /reset <number>  (e.g. /reset 2)")
            return
        tid = targets[idx]["id"]
        await DB.reset_progress(tid)
        state.reset_gen += 1
        state._last_scan = None
        await ev.reply(f"♻️ Progress reset for target {idx+1} ({tid}).\n"
                       "Next scan starts from POST 1. /start (or /resume) to begin.")

    @bot.on(events.NewMessage(pattern=r"^/goto\b"))
    async def goto_cmd(ev):
        if not await _admin(ev.sender_id):
            return
        targets = await DB.get_targets()
        if not targets:
            await ev.reply("Set a target first: /target")
            return
        parts = ev.raw_text.split(maxsplit=2)
        from scraper import parse_private_link
        tid = mid = None
        if len(parts) == 2:
            arg = parts[1]
            if arg.lstrip("-").isdigit():
                if len(targets) > 1:
                    listing = await _list_targets_text(scrape_client)
                    await ev.reply(f"{listing}\n\nMultiple targets — pick one:\n"
                                   f"/goto <number> <msg_id>  (e.g. /goto 2 120)\n"
                                   f"or use a message link: /goto https://t.me/c/<channel>/<msg>")
                    return
                tid, mid = targets[0]["id"], int(arg)
            else:
                cid, mid = parse_private_link(arg)
                if not mid:
                    await ev.reply("Couldn't parse that. Send /goto <msg_id>, /goto <n> <msg_id>, "
                                   "or /goto https://t.me/c/<channel>/<msg>")
                    return
                ids = [t["id"] for t in targets]
                if cid not in ids:
                    await ev.reply(f"⚠️ That link's channel ({cid}) is not in your targets. "
                                   "Add it with /target first.")
                    return
                tid = cid
        elif len(parts) == 3 and parts[1].isdigit():
            n = int(parts[1])
            if not (1 <= n <= len(targets)):
                await ev.reply(f"⚠️ Target number must be 1-{len(targets)}.")
                return
            arg = parts[2]
            tid = targets[n - 1]["id"]
            if arg.lstrip("-").isdigit():
                mid = int(arg)
            else:
                cid, mid = parse_private_link(arg)
                if not mid or cid != tid:
                    await ev.reply("⚠️ That link doesn't belong to the chosen target.")
                    return
        else:
            await ev.reply("Usage: /goto <msg_id> | /goto <target#> <msg_id> | /goto <message link>")
            return
        await DB.set_progress(tid, mid - 1)  # loop uses min_id=last_id -> starts AT mid
        state.reset_gen += 1
        state._last_scan = None
        await ev.reply(f"📌 Target {tid} will resume from message {mid}. /start or /resume to go.")


async def _bulk_edit(scrape_client, ev, channel, old, new):
    """/replace + /deletetext core — v25: TELEGRAM-SAFE PACING.
    Phase 1: scan (server-side search) and COLLECT all matching messages.
    Phase 2: a BACKGROUND worker edits ONE message every BULK_EDIT_DELAY
    seconds (default 2.5s) so Telegram's edit rate limit is never hit.
    FloodWaitError is slept through IN-PLACE and the SAME message retried
    (waits beyond BULK_MAX_FLOOD count as failures instead of parking
    forever). Live progress is edited into the status message every
    BULK_PROGRESS_EVERY edits. Returns the worker task (handlers ignore it)
    so the control bot stays responsive during long runs."""
    try:
        ent = await scrape_client.get_entity(channel)
    except Exception as e:
        await ev.reply(f"⚠️ Can't access that channel with the userbot account: {e}")
        return None
    title = getattr(ent, "title", str(channel))
    status = await ev.reply(f"🔍 Scanning **{title}** for messages containing: {old}…")
    matches = []
    try:
        async for m in scrape_client.iter_messages(ent, search=old):
            txt = m.message or ""
            if old not in txt:
                continue
            matches.append((m, txt))
    except Exception as e:
        try:
            await status.edit(f"⚠️ Scan failed ({e}).")
        except Exception:
            pass
        return None
    if not matches:
        await status.edit(f"✅ Nothing found in **{title}** containing: {old}")
        return None
    eta_min = len(matches) * BULK_EDIT_DELAY / 60
    await status.edit(
        f"📋 Found {len(matches)} message(s) in **{title}**.\n"
        f"Editing 1 every {BULK_EDIT_DELAY}s (Telegram-safe pacing) — ETA ~{eta_min:.1f} min.\n"
        "Flood waits are slept through automatically. Progress updates follow.\n"
        "\u23f8 Scraper auto-pauses while edits run (shared rate limit) and resumes after.")

    async def _worker():
        # v25.1: pause the scraper during bulk edits — Telegram's flood bucket
        # is ACCOUNT-WIDE; the scraper's sends share it (that caused the 219s
        # flood on the 99-message run while post 358 was being delivered).
        was_scraping = state.started and not state.paused
        if was_scraping:
            state.paused = True
        edited = failed = floods = 0
        last_err = None
        started = time.time()
        for m, txt in matches:
            new_txt = txt.replace(old, new)
            if not new:
                # /deletetext: tidy leftover double spaces / blank lines
                new_txt = re.sub(r"[ \t]{2,}", " ", new_txt)
                new_txt = re.sub(r"\n{3,}", "\n\n", new_txt).strip()
            if new_txt == txt:
                continue
            while True:
                try:
                    if not new_txt.strip():
                        # nothing left after the edit: Telegram forbids EMPTY text
                        # messages (MESSAGE_EMPTY). Media posts keep the file with
                        # an empty caption (legal); TEXT-ONLY posts are DELETED.
                        if getattr(m, "media", None) is not None:
                            await scrape_client.edit_message(ent, m, "", parse_mode=None)
                        else:
                            await scrape_client.delete_messages(ent, m)
                    else:
                        await scrape_client.edit_message(ent, m, new_txt, parse_mode=None)
                    edited += 1
                    break
                except FloodWaitError as fe:
                    secs = getattr(fe, "seconds", 60) or 60
                    if secs <= BULK_MAX_FLOOD:
                        floods += 1
                        try:
                            await status.edit(
                                f"⏳ Flood wait {secs}s from Telegram — sleeping it off "
                                f"(edited {edited}/{len(matches)} so far)…")
                        except Exception:
                            pass
                        await asyncio.sleep(secs + 5)
                        continue  # retry the SAME message after the wait
                    failed += 1
                    last_err = fe
                    break
                except Exception as e:
                    failed += 1
                    last_err = e
                    break
            await asyncio.sleep(BULK_EDIT_DELAY + random.uniform(0, 1.5))  # pacing + jitter — human-like, never a fixed burst pattern
            if edited and edited % BULK_PROGRESS_EVERY == 0:
                try:
                    await status.edit(f"⏳ Progress: {edited}/{len(matches)} edited"
                                      + (f", {failed} failed" if failed else "")
                                      + (f", {floods} flood wait(s) handled" if floods else ""))
                except Exception:
                    pass
        verb = "edited" if new else "cleaned"
        mins = (time.time() - started) / 60
        summary = (f"✅ Done — **{title}**: {verb} {edited}/{len(matches)} in {mins:.1f} min"
                   + (f", {floods} flood wait(s) slept through" if floods else ""))
        if failed:
            summary += f", {failed} failed (last error: {last_err})"
        try:
            await status.edit(summary)
        except Exception:
            pass
        if was_scraping:
            state.paused = False
            try:
                await ev.reply("▶️ Scraper resumed — bulk edit finished.")
            except Exception:
                pass

    return asyncio.ensure_future(_worker())


async def start(scrape_client):
    global bot
    bot = TelegramClient(MemorySession(), API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    register(scrape_client)
    await _menu()
    return bot
