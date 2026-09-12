"""botapi.py — control BOT (BOT_TOKEN from BotFather) with a real tappable
command menu. /target /bypass /adddb run as a wizard: tap the command, the bot
asks for the id, you send it, it validates + saves. v3: /ping added.
Only ADMIN_USER_ID (your numeric Telegram id) can use it."""
from telethon import TelegramClient, events
from telethon.sessions import MemorySession
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN_USER_ID
import db as DB
from flow import state

bot = None  # created lazily inside start() — creating a client at import time
# crashes on Python 3.14 ("no current event loop in thread 'MainThread'")

_CMDS = [
    ("ping",     "Check the bot is alive"),
    ("target",   "Add a target channel (wizard)"),
    ("targets",  "List target channels"),
    ("deltarget","Remove a target channel (wizard)"),
    ("bypass",   "Set bypass group (wizard)"),
    ("adddb",    "Set database channel (wizard)"),
    ("lastpost", "Show newest post in target channel"),
    ("start",    "Start the scraper"),
    ("pause",    "Pause scraping"),
    ("resume",   "Resume scraping"),
    ("status",   "Live stage & config"),
    ("current",  "Current post & stage"),
    ("progress", "Stats, last post, failure reasons"),
    ("skip",     "Skip current post"),
    ("stop",     "Stop the scraper"),
    ("reset",    "Reset progress — scrape from post 1"),
    ("goto",     "Start from a specific message id"),
]

_pending = {}


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


def register(scrape_client):
    @bot.on(events.NewMessage(pattern=r"^/ping$"))
    async def ping_cmd(ev):
        if _admin(ev.sender_id):
            await ev.reply(f"\U0001F3D3 Pong — control bot + scraper alive.\n"
                           f"📍 Stage: {state.stage} | Running: {state.running} | Paused: {state.paused}")

    @bot.on(events.NewMessage(pattern=r"^/(target|bypass|adddb|deltarget)$"))
    async def wizard(ev):
        if not _admin(ev.sender_id):
            return
        cmd = ev.raw_text[1:]
        if cmd == "deltarget":
            targets = await DB.get_targets()
            if not targets:
                await ev.reply("No target channels set. Add one with /target")
                return
            _pending[ev.sender_id] = "deltarget"
            listing = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(targets))
            await ev.reply(f"Current targets:\n{listing}\n\nSend the id (or number) to REMOVE.\nCancel: /cancel")
            return
        field = {"target": "target_id", "bypass": "bypass_id", "adddb": "db_id"}[cmd]
        _pending[ev.sender_id] = field
        hint = "the channel to scrape (adds to your list)" if cmd == "target" else field
        await ev.reply(f"Send me the **{hint}** now (numeric id like -100… or @username).\nCancel: /cancel")

    @bot.on(events.NewMessage(pattern=r"^/cancel$"))
    async def cancel(ev):
        _pending.pop(ev.sender_id, None)
        await ev.reply("Cancelled.")

    @bot.on(events.NewMessage())
    async def wizard_answer(ev):
        field = _pending.get(ev.sender_id)
        if not field or not _admin(ev.sender_id) or ev.raw_text.startswith("/"):
            return  # a new /command cancels the pending wizard instead of being eaten
        if field == "deltarget":
            targets = await DB.get_targets()
            raw = ev.raw_text.strip()
            if raw.isdigit() and 1 <= int(raw) <= len(targets):
                v = targets[int(raw) - 1]          # pick by list number
            else:
                v = _parse_id(raw)
            if v not in targets:
                await ev.reply(f"⚠️ {v} is not in your targets list.")
                return
            remaining = await DB.remove_target(v)
            _pending.pop(ev.sender_id, None)
            await ev.reply(f"🗑 Removed {v}.\nTargets left: {remaining or 'none'}")
            return
        v = _parse_id(ev.raw_text)
        try:
            await scrape_client.get_entity(v)
        except Exception as e:
            await ev.reply(f"⚠️ Can't access that chat with the userbot account: {e}")
            return
        if field == "target_id":
            targets = await DB.add_target(v)
            _pending.pop(ev.sender_id, None)
            await ev.reply(f"✅ Target added: {v}\nAll targets: {targets}\nUse /targets to list, /deltarget to remove, /start to scrape.")
            return
        await DB.set_config(field, v)
        _pending.pop(ev.sender_id, None)
        await ev.reply(f"✅ Saved {field} = {v}\nNext: /target /bypass /adddb or /start")

    @bot.on(events.NewMessage(pattern=r"^/targets$"))
    async def targets_cmd(ev):
        if not _admin(ev.sender_id):
            return
        targets = await DB.get_targets()
        if not targets:
            await ev.reply("No target channels. Add one with /target")
            return
        lines = ["🎯 Target channels:"]
        for i, t in enumerate(targets):
            prog = await DB.get_progress(t)
            lines.append(f"  {i+1}. {t} — resume at msg {prog}")
        lines.append("\n/deltarget to remove one.")
        await ev.reply("\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"^/lastpost$"))
    async def lastpost(ev):
        if not _admin(ev.sender_id):
            return
        targets = await DB.get_targets()
        if not targets:
            await ev.reply("Set a target first: /target")
            return
        cfg = await DB.get_config()
        from scraper import is_post, find_button
        from config import BTN_DOWNLOAD
        total = 0
        newest = None
        async for m0 in scrape_client.iter_messages(targets[0], limit=500):
            total += 1
            if newest is None and is_post(m0):
                newest = m0
        resume_at = await DB.get_progress(targets[0])
        if newest:
            m = newest
            btn = find_button(m, BTN_DOWNLOAD)
            cap = (m.message or "")[:200].replace("\n", " ")
            await ev.reply("📄 Target channel overview\n• messages scanned: " + str(total)
                           + "\n• newest post msg id: " + str(m.id)
                           + "\n• date: " + m.date.strftime("%Y-%m-%d %H:%M UTC")
                           + "\n• caption: " + cap
                           + "\n• Download button: " + ("yes — " + btn[2].text if btn else "NO")
                           + "\n• current resume point: " + str(resume_at)
                           + "\n\nTip: /reset to scrape from post 1, /goto <id> to start elsewhere.")
            return
        await ev.reply("Scanned " + str(total) + " messages — none look like posts "
                       "(photo+caption+Download button). Check the channel or button text.")
        return
        async for m in scrape_client.iter_messages(cfg["target_id"], limit=30):
            if is_post(m):
                btn = find_button(m, BTN_DOWNLOAD)
                cap = (m.message or "")[:200].replace("\n", " ")
                await ev.reply("📄 Last post in target channel\n• msg id: " + str(m.id)
                               + "\n• date: " + m.date.strftime("%Y-%m-%d %H:%M UTC")
                               + "\n• caption: " + cap
                               + "\n• Download button: " + ("yes — " + btn[2].text if btn else "NO"))
                return
        await ev.reply("No qualifying post found in the last 30 messages.")

    @bot.on(events.NewMessage(pattern=r"^/start$"))
    async def start_cmd(ev):
        if _admin(ev.sender_id):
            state.abort = False; state.paused = False; state.started = True
            cfg = await DB.get_config()
            missing = [k for k in ("target_id", "bypass_id", "db_id") if not cfg.get(k)]
            if missing:
                await ev.reply("⚠️ Cannot start — missing: " + ", ".join(missing)
                               + "\nSet them with /target /bypass /adddb first.")
                state.started = False
                return
            await ev.reply("▶️ Scraper started. It will now scan the target channel from the first post.\nCheck /progress anytime.")

    @bot.on(events.NewMessage(pattern=r"^/pause$"))
    async def pause_cmd(ev):
        if _admin(ev.sender_id):
            state.paused = True
            await ev.reply("⏸ Paused. Progress is saved in MongoDB — safe even if Render crashes. /resume to continue.")

    @bot.on(events.NewMessage(pattern=r"^/resume$"))
    async def resume_cmd(ev):
        if _admin(ev.sender_id):
            state.paused = False; state.abort = False; state.started = True
            last = await DB.get_progress((await DB.get_config()).get("target_id") or 0)
            await ev.reply(f"▶️ Resumed from message id {last}.")

    @bot.on(events.NewMessage(pattern=r"^/(status|current)$"))
    async def status_cmd(ev):
        if _admin(ev.sender_id):
            cfg = await DB.get_config()
            await ev.reply(f"🤖 Running: {state.running} | Paused: {state.paused}\n"
                           f"📍 Stage: {state.stage}\n📄 Current post: {state.current_post}\n"
                           f"🎯 Targets: {await DB.get_targets()}\n🔁 Bypass: {cfg.get('bypass_id')}\n"
                           f"🗄 DB: {cfg.get('db_id')}")

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

    @bot.on(events.NewMessage(pattern=r"^/reset$"))
    async def reset_cmd(ev):
        if not _admin(ev.sender_id):
            return
        cfg = await DB.get_config()
        tid = cfg.get("target_id")
        if not tid:
            await ev.reply("Set the target first: /target")
            return
        await DB.reset_progress(tid)
        state._last_scan = None
        await ev.reply("♻️ Progress reset for the target channel.\n"
                       "Next scan starts from POST 1. Use /start (or /resume) to begin.")

    @bot.on(events.NewMessage(pattern=r"^/goto\b"))
    async def goto_cmd(ev):
        if not _admin(ev.sender_id):
            return
        parts = ev.raw_text.split()
        cfg = await DB.get_config()
        tid = cfg.get("target_id")
        if len(parts) != 2 or not tid:
            await ev.reply("Usage: /goto <message_id> or /goto https://t.me/c/<channel>/<msg>")
            return
        arg = parts[1]
        if arg.lstrip("-").isdigit():
            mid = int(arg)
        else:
            from scraper import parse_private_link
            chat_id, mid = parse_private_link(arg)
            if not mid:
                await ev.reply("Couldn't parse that. Send a message id (e.g. /goto 120) "
                               "or a message link like https://t.me/c/2514892126/120")
                return
            if chat_id != tid:
                await ev.reply(f"⚠️ That link is for channel {chat_id}, but your target is {tid}. "
                               "Fix with /target first.")
                return
        await DB.set_progress(tid, mid - 1)  # loop uses min_id=last_id -> starts AT mid
        state._last_scan = None
        await ev.reply(f"📌 Resume point set to message {mid}. /start or /resume to go.")


async def start(scrape_client):
    global bot
    bot = TelegramClient(MemorySession(), API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    register(scrape_client)
    await _menu()
    return bot
