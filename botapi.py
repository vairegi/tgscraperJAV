"""botapi.py — control BOT (BOT_TOKEN from BotFather) with a real tappable
command menu. Menu commands are no-argument; IDs are added via the setup
wizard: tap /target -> the bot asks for the id -> you send it -> saved.
Only ADMIN_USER_ID (your numeric Telegram id) can use it."""
import asyncio
from telethon import TelegramClient, events, Button
from telethon.sessions import MemorySession
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN_USER_ID
import db as DB
from flow import state

# Event loop creation for Python 3.14+
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

bot = TelegramClient(MemorySession(), API_ID, API_HASH, loop=loop)

_CMDS = [
    ("target",   "Set target channel (wizard)"),
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
]

_pending = {}  # user_id -> field awaiting id

def _admin(uid):
    return ADMIN_USER_ID == 0 or uid == ADMIN_USER_ID

async def _menu():
    await bot(SetBotCommandsRequest(
        scope=BotCommandScopeDefault(),
        lang_code="",
        commands=[BotCommand(c, d) for c, d in _CMDS],
    ))

def _parse_id(raw):
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError:
        return raw.lstrip("@")

async def _fmt_progress():
    from bot import fmt_progress  # shared formatter
    return await fmt_progress()

def register(scrape_client):
    @bot.on(events.NewMessage(pattern=r"^/(target|bypass|adddb)$"))
    async def wizard(ev):
        if not _admin(ev.sender_id):
            return
        field = {"target": "target_id", "bypass": "bypass_id", "adddb": "db_id"}[
            ev.raw_text[1:]]
        _pending[ev.sender_id] = field
        await ev.reply(
            f"Send me the **{field}** now (numeric id like -100… or @username).\n"
            "Cancel: /cancel")

    @bot.on(events.NewMessage(pattern=r"^/cancel$"))
    async def cancel(ev):
        _pending.pop(ev.sender_id, None)
        await ev.reply("Cancelled.")

    @bot.on(events.NewMessage())
    async def wizard_answer(ev):
        field = _pending.get(ev.sender_id)
        if not field or not _admin(ev.sender_id) or ev.raw_text.startswith("/"):
            return
        v = _parse_id(ev.raw_text)
        try:
            await scrape_client.get_entity(v)  # validate the account can see it
        except Exception as e:
            await ev.reply(f"⚠️ Can't access that chat with the userbot account: {e}")
            return
        await DB.set_config(field, v)
        _pending.pop(ev.sender_id, None)
        await ev.reply(f"✅ Saved {field} = {v}\nNext: /target /bypass /adddb or /start")

    @bot.on(events.NewMessage(pattern=r"^/lastpost$"))
    async def lastpost(ev):
        if not _admin(ev.sender_id):
            return
        cfg = await DB.get_config()
        if not cfg.get("target_id"):
            await ev.reply("Set the target first: /target")
            return
        from scraper import is_post, find_button
        from config import BTN_DOWNLOAD
        async for m in scrape_client.iter_messages(cfg["target_id"], limit=30):
            if is_post(m):
                btn = find_button(m, BTN_DOWNLOAD)
                await ev.reply(
                    f"📄 Last post in target channel\n"
                    f"• msg id: {m.id}\n"
                    f"• date: {m.date:%Y-%m-%d %H:%M UTC}\n"
                    f"• caption: {(m.message or '')[:200]}\n"
                    f"• Download button: {'yes — ' + btn[2].text if btn else 'NO'}")
                return
        await ev.reply("No qualifying post found in the last 30 messages.")

    @bot.on(events.NewMessage(pattern=r"^/start$"))
    async def start_cmd(ev):
        if _admin(ev.sender_id):
            state.abort = False; state.paused = False
            await ev.reply("▶️ Scraper started.")

    @bot.on(events.NewMessage(pattern=r"^/pause$"))
    async def pause_cmd(ev):
        if _admin(ev.sender_id):
            state.paused = True
            await ev.reply("⏸ Paused. Progress is saved in MongoDB — safe even if Render crashes. /resume to continue.")

    @bot.on(events.NewMessage(pattern=r"^/resume$"))
    async def resume_cmd(ev):
        if _admin(ev.sender_id):
            state.paused = False; state.abort = False
            last = await DB.get_progress((await DB.get_config()).get("target_id") or 0)
            await ev.reply(f"▶️ Resumed from message id {last}.")

    @bot.on(events.NewMessage(pattern=r"^/(status|current)$"))
    async def status_cmd(ev):
        if _admin(ev.sender_id):
            cfg = await DB.get_config()
            await ev.reply(
                f"🤖 Running: {state.running} | Paused: {state.paused}\n"
                f"📍 Stage: {state.stage}\n📄 Current post: {state.current_post}\n"
                f"🎯 Target: {cfg.get('target_id')}\n🔁 Bypass: {cfg.get('bypass_id')}\n"
                f"🗄 DB: {cfg.get('db_id')}")

    @bot.on(events.NewMessage(pattern=r"^/progress$"))
    async def progress_cmd(ev):
        if _admin(ev.sender_id):
            await ev.reply(await _fmt_progress())

    @bot.on(events.NewMessage(pattern=r"^/skip$"))
    async def skip_cmd(ev):
        if _admin(ev.sender_id):
            state.abort = True
            await ev.reply("⏭ Skipping current post…")

    @bot.on(events.NewMessage(pattern=r"^/stop$"))
    async def stop_cmd(ev):
        if _admin(ev.sender_id):
            state.abort = True; state.running = False
            await ev.reply("🛑 Stopped.")

async def start(scrape_client):
    await bot.start(bot_token=BOT_TOKEN)
    register(scrape_client)
    await _menu()
    return bot
