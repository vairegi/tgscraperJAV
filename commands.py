"""commands.py — userbot commands (type anywhere, Saved Messages recommended).
v3: /ping added. The control bot (botapi.py) offers the same commands via a
tappable menu."""
from telethon import events
import db as DB
from flow import state


def parse_id(raw):
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError:
        return raw.lstrip("@")


async def reply(ev, text):
    await ev.reply(text, parse_mode=None)


def register(client):
    def is_cmd(name):
        return events.NewMessage(outgoing=True, pattern=rf"^/{name}(\s|$)")

    @client.on(is_cmd("ping"))
    async def _(ev):
        await reply(ev, f"\U0001F3D3 Pong — userbot alive.\n📍 Stage: {state.stage} | Running: {state.running} | Paused: {state.paused}")

    @client.on(is_cmd("target"))
    async def _(ev):
        v = parse_id(ev.raw_text.split(None, 1)[1])
        await DB.set_config("target_id", v)
        await reply(ev, f"✅ Target channel set: {v}")

    @client.on(is_cmd("bypass"))
    async def _(ev):
        v = parse_id(ev.raw_text.split(None, 1)[1])
        await DB.set_config("bypass_id", v)
        await reply(ev, f"✅ Bypass group set: {v}")

    @client.on(is_cmd("adddb"))
    async def _(ev):
        v = parse_id(ev.raw_text.split(None, 1)[1])
        await DB.set_config("db_id", v)
        await reply(ev, f"✅ Database channel set: {v}")

    @client.on(is_cmd("lastpost"))
    async def _(ev):
        cfg = await DB.get_config()
        if not cfg.get("target_id"):
            await reply(ev, "Set a target first: /target <id>")
            return
        from scraper import is_post as _is_post
        async for m in client.iter_messages(cfg["target_id"], limit=30):
            if _is_post(m):
                cap = (m.message or "")[:200].replace("\n", " ")
                await reply(ev, "📄 Last post in target channel\n• msg id: " + str(m.id)
                            + "\n• date: " + m.date.strftime("%Y-%m-%d %H:%M UTC")
                            + "\n• caption: " + cap)
                return
        await reply(ev, "No qualifying post found in the last 30 messages.")

    @client.on(is_cmd("start"))
    async def _(ev):
        state.abort = False
        state.paused = False
        await reply(ev, "▶️ Scraper started (or resumed).")

    @client.on(is_cmd("status"))
    async def _(ev):
        cfg = await DB.get_config()
        await reply(ev,
            f"🤖 Running: {state.running} | Paused: {state.paused}\n"
            f"📍 Stage: {state.stage}\n📄 Current post: {state.current_post}\n"
            f"🎯 Target: {cfg.get('target_id')}\n🔁 Bypass: {cfg.get('bypass_id')}\n"
            f"🗄 DB channel: {cfg.get('db_id')}")

    @client.on(is_cmd("current"))
    async def _(ev):
        await reply(ev, f"📄 Post: {state.current_post}\n📍 Stage: {state.stage}")

    @client.on(is_cmd("progress"))
    async def _(ev):
        from bot import fmt_progress
        await reply(ev, await fmt_progress())

    @client.on(is_cmd("skip"))
    async def _(ev):
        state.abort = True
        await reply(ev, "⏭ Skipping current post…")

    @client.on(is_cmd("pause"))
    async def _(ev):
        state.paused = True
        await reply(ev, "⏸ Paused. Progress is saved in MongoDB — if Render crashes, it auto-resumes from the same post. /resume to continue.")

    @client.on(is_cmd("resume"))
    async def _(ev):
        state.paused = False
        cfg = await DB.get_config()
        last = await DB.get_progress(cfg.get("target_id")) if cfg.get("target_id") else 0
        await reply(ev, f"▶️ Resumed — continuing from message id {last}.")

    @client.on(is_cmd("stop"))
    async def _(ev):
        state.abort = True
        state.running = False
        await reply(ev, "🛑 Stopped.")
