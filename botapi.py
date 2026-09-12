"""botapi.py — control BOT (BOT_TOKEN from BotFather) with a tappable menu.
v13: per-target DB channels. /target wizard now asks for the channel id AND
then its DB channel; /setdb adjusts a target's DB later; /reset and /goto are
per-target; all wizards accept inline args (/adddb -100…); /help lists every
command. Only ADMIN_USER_ID can use it."""
from telethon import TelegramClient, events
from telethon.sessions import MemorySession
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN_USER_ID
import db as DB
from flow import state

bot = None  # created lazily inside start() (Py3.14 has no loop at import time)

_CMDS = [
    ("help",     "Show all commands"),
    ("ping",     "Check the bot is alive"),
    ("target",   "Add target channel + its DB channel (wizard)"),
    ("targets",  "List targets -> their DB channels + progress"),
    ("deltarget","Remove a target channel"),
    ("setdb",    "Change a target's DB channel"),
    ("adddb",    "Set the fallback DB channel"),
    ("bypass",   "Set bypass group"),
    ("goto",     "Set a target's start message (/goto <n> <msg> or link)"),
    ("reset",    "Reset a target's progress to post 1"),
    ("lastpost", "Newest post in a target channel"),
    ("start",    "Start scraping"),
    ("pause",    "Pause scraping"),
    ("resume",   "Resume scraping"),
    ("status",   "Live stage & config"),
    ("current",  "Current post & stage"),
    ("progress", "Stats, last post, failure reasons"),
    ("skip",     "Skip current post"),
    ("stop",     "Stop the scraper"),
]

_pending = {}  # user_id -> (kind, extra)


def _admin(uid):
    return ADMIN_USER_ID == 0 or uid == ADMIN_USER_ID


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


async def _list_targets_text():
    targets = await DB.get_targets()
    if not targets:
        return None
    lines = ["🎯 Target channels:"]
    for i, t in enumerate(targets):
        prog = await DB.get_progress(t["id"])
        db_txt = t["db_id"] if t["db_id"] else "(fallback /adddb)"
        lines.append(f"  {i+1}. {t['id']} → DB {db_txt} — resume at msg {prog}")
    return "\n".join(lines)


async def _add_target_with_db(scrape_client, ev, tid, dbid):
    try:
        await scrape_client.get_entity(tid)
    except Exception as e:
        await ev.reply(f"⚠️ Can't access that target with the userbot account: {e}")
        return
    try:
        await scrape_client.get_entity(dbid)
    except Exception as e:
        await ev.reply(f"⚠️ Can't access that DB channel with the userbot account: {e}")
        return
    await DB.add_target(tid, dbid)
    await ev.reply(f"✅ Target saved:\n  {tid} → DB {dbid}\n\n/targets to view all, /start to scrape.")


def register(scrape_client):
    # ---------- info ----------
    @bot.on(events.NewMessage(pattern=r"^/help$"))
    async def help_cmd(ev):
        if not _admin(ev.sender_id):
            return
        lines = ["📖 COMMANDS"]
        for c, d in _CMDS:
            lines.append(f"/{c} — {d}")
        lines.append("\nTips: /target walks you through channel + its DB channel. "
                     "/goto accepts a message link (auto-picks the right target). "
                     "Caught-up channels re-scan for new posts every 30s.")
        await ev.reply("\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"^/ping$"))
    async def ping_cmd(ev):
        if _admin(ev.sender_id):
            await ev.reply(f"🏓 Pong — control bot + scraper alive.\n"
                           f"📍 Stage: {state.stage} | Running: {state.running} | Paused: {state.paused}")

    # ---------- wizards (all accept inline args too) ----------
    @bot.on(events.NewMessage(pattern=r"^/target(?:\s+(.+))?$"))
    async def target_cmd(ev):
        if not _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        if arg:
            _pending[ev.sender_id] = ("target_db", _parse_id(arg))
            await ev.reply(f"Target: **{_parse_id(arg)}**\nNow send the DB channel id for THIS target "
                           f"(where its videos+srt go).\nCancel: /cancel")
            return
        _pending[ev.sender_id] = ("target_id", None)
        await ev.reply("Send me the TARGET channel id (numeric like -100… or @username).\nCancel: /cancel")

    @bot.on(events.NewMessage(pattern=r"^/(bypass|adddb)(?:\s+(.+))?$"))
    async def simple_wizard(ev):
        if not _admin(ev.sender_id):
            return
        cmd = ev.pattern_match.group(1)
        arg = (ev.pattern_match.group(2) or "").strip()
        field = {"bypass": "bypass_id", "adddb": "db_id"}[cmd]
        if arg:
            await _save_simple(ev, field, _parse_id(arg))
            return
        _pending[ev.sender_id] = (field, None)
        label = "bypass group" if field == "bypass_id" else "fallback DB channel"
        await ev.reply(f"Send me the {label} id (numeric like -100… or @username).\nCancel: /cancel")

    async def _save_simple(ev, field, v):
        try:
            await scrape_client.get_entity(v)
        except Exception as e:
            await ev.reply(f"⚠️ Can't access that chat with the userbot account: {e}")
            return
        await DB.set_config(field, v)
        await ev.reply(f"✅ Saved {field} = {v}")

    @bot.on(events.NewMessage(pattern=r"^/deltarget(?:\s+(.+))?$"))
    async def deltarget_cmd(ev):
        if not _admin(ev.sender_id):
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
        listing = await _list_targets_text()
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
        if not _admin(ev.sender_id):
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
        listing = await _list_targets_text()
        await ev.reply(f"{listing}\n\nSend: <number> <db_id>  (e.g.  2 -100999888777)\nCancel: /cancel")

    async def _do_setdb(ev, arg):
        parts = arg.split()
        targets = await DB.get_targets()
        if len(parts) != 2 or not parts[0].isdigit() or not (1 <= int(parts[0]) <= len(targets)):
            await ev.reply("⚠️ Format: <target number> <db_id> — e.g.  2 -100999888777")
            return
        t = targets[int(parts[0]) - 1]
        dbid = _parse_id(parts[1])
        try:
            await scrape_client.get_entity(dbid)
        except Exception as e:
            await ev.reply(f"⚠️ Can't access that DB channel with the userbot account: {e}")
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
        if not item or not _admin(ev.sender_id) or ev.raw_text.startswith("/"):
            return  # a new /command cancels the pending wizard instead of being eaten
        kind, extra = item
        if kind == "target_id":
            tid = _parse_id(ev.raw_text)
            try:
                await scrape_client.get_entity(tid)
            except Exception as e:
                await ev.reply(f"⚠️ Can't access that channel with the userbot account: {e}")
                return
            _pending[ev.sender_id] = ("target_db", tid)
            await ev.reply(f"Target: **{tid}**\nNow send the DB channel id for THIS target "
                           f"(where its videos+srt go).\nCancel: /cancel")
            return
        if kind == "target_db":
            dbid = _parse_id(ev.raw_text)
            _pending.pop(ev.sender_id, None)
            await _add_target_with_db(scrape_client, ev, extra, dbid)
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
        await _save_simple(ev, kind, _parse_id(ev.raw_text))

    # ---------- view ----------
    @bot.on(events.NewMessage(pattern=r"^/targets$"))
    async def targets_cmd(ev):
        if not _admin(ev.sender_id):
            return
        listing = await _list_targets_text()
        if not listing:
            await ev.reply("No target channels. Add one with /target")
            return
        await ev.reply(listing + "\n\n/setdb to change a DB, /deltarget to remove.")

    @bot.on(events.NewMessage(pattern=r"^/lastpost(?:\s+(\d+))?$"))
    async def lastpost(ev):
        if not _admin(ev.sender_id):
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
        if not _admin(ev.sender_id):
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

    @bot.on(events.NewMessage(pattern=r"^/pause$"))
    async def pause_cmd(ev):
        if _admin(ev.sender_id):
            state.paused = True
            await ev.reply("⏸ Paused. Progress is saved in MongoDB — safe even if Render crashes. /resume to continue.")

    @bot.on(events.NewMessage(pattern=r"^/resume$"))
    async def resume_cmd(ev):
        if _admin(ev.sender_id):
            state.paused = False; state.abort = False; state.started = True
            await ev.reply("▶️ Resumed from saved progress.")

    @bot.on(events.NewMessage(pattern=r"^/(status|current)$"))
    async def status_cmd(ev):
        if _admin(ev.sender_id):
            cfg = await DB.get_config()
            listing = await _list_targets_text() or "  (none)"
            await ev.reply(f"🤖 Running: {state.running} | Paused: {state.paused}\n"
                           f"📍 Stage: {state.stage}\n📄 Current post: {state.current_post}\n"
                           f"{listing}\n🔁 Bypass: {cfg.get('bypass_id')}\n"
                           f"🗄 Fallback DB: {cfg.get('db_id')}")

    @bot.on(events.NewMessage(pattern=r"^/progress$"))
    async def progress_cmd(ev):
        if _admin(ev.sender_id):
            from bot import fmt_progress
            await ev.reply(await fmt_progress())

    @bot.on(events.NewMessage(pattern=r"^/skip$"))
    async def skip_cmd(ev):
        if _admin(ev.sender_id):
            state.abort = True
            await ev.reply("⏭ Skipping current post…")

    @bot.on(events.NewMessage(pattern=r"^/stop$"))
    async def stop_cmd(ev):
        if _admin(ev.sender_id):
            state.abort = True; state.running = False; state.started = False
            await ev.reply("🛑 Stopped. Progress saved — /resume or /start continues from the same post.")

    # ---------- per-target reset & goto ----------
    @bot.on(events.NewMessage(pattern=r"^/reset(?:\s+(\d+))?$"))
    async def reset_cmd(ev):
        if not _admin(ev.sender_id):
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
        state._last_scan = None
        await ev.reply(f"♻️ Progress reset for target {idx+1} ({tid}).\n"
                       "Next scan starts from POST 1. /start (or /resume) to begin.")

    @bot.on(events.NewMessage(pattern=r"^/goto\b"))
    async def goto_cmd(ev):
        if not _admin(ev.sender_id):
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
                    listing = await _list_targets_text()
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
        state._last_scan = None
        await ev.reply(f"📌 Target {tid} will resume from message {mid}. /start or /resume to go.")


async def start(scrape_client):
    global bot
    bot = TelegramClient(MemorySession(), API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    register(scrape_client)
    await _menu()
    return bot
