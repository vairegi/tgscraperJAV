"""bot.py — entry point: session, commands, health server, main scrape loop.
v3: FloodWait-safe startup — on FloodWaitError the process SLEEPS IN-PLACE
(instead of crashing), so Render never enters a crash-restart loop that keeps
refreshing Telegram's flood timer. Clean task shutdown (no 'coroutine never
awaited' warnings). Py3.14-compatible event loop creation."""
import asyncio, logging, sys, time
from aiohttp import web
from telethon.errors import FloodWaitError
from session_manager import SessionManager
from scraper import is_post, why_not_post
from flow import process_post, state, Abort
import commands, db as DB
import botapi

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tgscraper")

MAX_AUTH_RETRIES = 3


async def guarded(factory, name):
    """Await factory() with FloodWait protection. factory must return a FRESH
    coroutine each attempt (coroutines cannot be re-awaited). On FloodWait we
    sleep e.seconds INSIDE the process — Render sees a live process, so no
    restart loop and no re-auth spam against Telegram's servers."""
    for attempt in range(1, MAX_AUTH_RETRIES + 1):
        try:
            return await factory()
        except FloodWaitError as e:
            wait = e.seconds + 5
            log.warning("%s: FloodWaitError — sleeping %ds in-process (attempt %d/%d)",
                        name, wait, attempt, MAX_AUTH_RETRIES)
            if attempt == MAX_AUTH_RETRIES:
                log.error("%s: still flood-limited after %d attempts — parking "
                          "15 min then exiting cleanly (Render restarts AFTER "
                          "Telegram's timer expires, no loop).", name, attempt)
                await asyncio.sleep(900)
                sys.exit(0)
            await asyncio.sleep(wait)


def _fmt_ts(ts):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


async def fmt_progress():
    s = await DB.get_stats()
    cfg = await DB.get_config()
    tid = cfg.get("target_id")
    last = await DB.get_progress(tid) if tid else 0
    last_post = await DB.get_last_post(tid) if tid else None
    fails = await DB.get_failures(5)
    lines = [
        "\U0001F4CA PROGRESS",
        f"\U0001F4CD Stage: {state.stage}",
        f"\U0001F4C4 Current post: {state.current_post}",
        f"\u2705 Last scraped post: {last_post}",
        f"\u23ED Next resume point (msg id): {last}",
        f"\U0001F4C1 Posts scraped: {s.get('posts_done', 0)}",
        f"\U0001F3AC Videos sent: {s.get('videos_sent', 0)}",
        f"\U0001F4AC SRT sent: {s.get('srt_sent', 0)}",
        f"\u274C Failures: {s.get('failures', 0)}",
    ]
    if fails:
        lines.append("— Recent failures —")
        for f in fails:
            lines.append(f"\u2022 post {f['post_id']} @ {f['stage']}: {f['reason']} ({_fmt_ts(f['ts'])})")
    return "\n".join(lines)


async def health(_):
    return web.json_response({"ok": True, "stage": state.stage,
                              "running": state.running, "post": state.current_post})


async def start_health_server(port):
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("health server on :%s", port)


async def scrape_loop(client):
    """Scan target channel oldest->newest, processing real posts.
    Progress is persisted to MongoDB after EVERY message — a Render crash or
    redeploy resumes from the exact same post."""
    while True:
        await asyncio.sleep(3)
        if not state.started or state.paused:
            state.running = False
            continue
        cfg = await DB.get_config()
        missing = [k for k in ("target_id", "bypass_id", "db_id") if not cfg.get(k)]
        if missing:
            state.running = False
            if state.stage != "waiting config":
                log.warning("started but config missing: %s — set via control bot", missing)
                state.stage = "waiting config"
            continue
        if not state.running:
            log.info("scraper ACTIVE — target=%s bypass=%s db=%s", cfg["target_id"], cfg["bypass_id"], cfg["db_id"])
        state.running = True
        target = cfg["target_id"]
        last_id = await DB.get_progress(target)
        if getattr(state, "_last_scan", None) != (target, last_id):
            log.info("scanning target %s from message id %s (oldest -> newest)", target, last_id)
            state._last_scan = (target, last_id)
        try:
            async for msg in client.iter_messages(target, min_id=last_id, reverse=True):
                while state.paused and not state.abort:
                    await asyncio.sleep(2)
                if state.abort:
                    state.abort = False
                    break
                if not is_post(msg):
                    log.info("skip msg %s (%s)", msg.id, why_not_post(msg))
                    await DB.set_progress(target, msg.id)
                    continue
                log.info("POST FOUND: msg %s — starting download flow", msg.id)
                state.current_post = msg.id
                try:
                    await process_post(client, cfg, msg)
                    await DB.set_progress(target, msg.id)
                    log.info("post %s done", msg.id)
                except Abort:
                    await DB.add_failure(msg.id, state.stage, "skipped by user")
                    await DB.set_progress(target, msg.id)
                    state.abort = False
                except FloodWaitError as e:
                    # Telegram rate-limit mid-scrape: sleep in-process, retry SAME post.
                    # If the wait is huge, park paused instead of burning a long sleep.
                    from config import FLOOD_MAX_WAIT, FLOOD_PARK
                    if e.seconds > FLOOD_MAX_WAIT:
                        state.paused = True
                        log.warning("FloodWait %ds on post %s exceeds cap %ds — PAUSING scraper for %ds "
                                    "(it auto-resumes after; progress already saved)", e.seconds, msg.id,
                                    FLOOD_MAX_WAIT, FLOOD_PARK)
                        await asyncio.sleep(FLOOD_PARK)
                        state.paused = False
                    else:
                        log.warning("scrape FloodWait %ds on post %s — sleeping", e.seconds, msg.id)
                        await asyncio.sleep(e.seconds + 5)
                    continue
                except Exception as e:
                    log.exception("post %s failed", msg.id)
                    await DB.add_failure(msg.id, state.stage, e)
                    await DB.set_progress(target, msg.id)
                state.current_post = None
                state.stage = "idle"
                from config import POST_DELAY
                await asyncio.sleep(POST_DELAY)  # pacing: one post at a time, ban-safe
            if state.stage != "watching for new posts":
                log.info("scan pass complete (caught up to latest message); watching for new posts")
                state.stage = "watching for new posts"
            await asyncio.sleep(30)  # poll for new posts
        except FloodWaitError as e:
            log.warning("scrape loop FloodWait %ds — sleeping in-process", e.seconds)
            await asyncio.sleep(e.seconds + 5)
        except Exception:
            log.exception("scrape loop error")
            await asyncio.sleep(15)


async def main():
    from config import PORT, BOT_TOKEN
    sm = SessionManager()
    client = await guarded(lambda: sm.start(), "userbot login")
    me = await client.get_me()
    log.info("logged in as %s (%s)", me.first_name, me.id)
    # NOTE: userbot command handlers are DISABLED (bot-only replies).
    # commands.register(client)  <- uncomment to re-enable Saved-Messages commands
    await start_health_server(PORT)
    tasks = [
        asyncio.ensure_future(scrape_loop(client)),
        asyncio.ensure_future(client.run_until_disconnected()),
    ]
    if BOT_TOKEN:
        ctl = await guarded(lambda: botapi.start(client), "control bot login")
        me_b = await ctl.get_me()
        log.info("control bot @%s online (command menu registered)", me_b.username)
        tasks.append(asyncio.ensure_future(ctl.run_until_disconnected()))
    else:
        log.info("BOT_TOKEN not set — control bot/menu disabled, userbot commands still work")
    try:
        await asyncio.gather(*tasks)
    finally:
        # clean shutdown: cancel + await every task so nothing is 'never awaited'
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def run():
    loop = asyncio.new_event_loop()          # Py3.14-compatible loop creation
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    except FloodWaitError as e:
        # last-resort guard: sleep out the timer, then exit 0 — Render's restart
        # happens AFTER the flood window, so no restart loop
        secs = e.seconds + 5
        log.warning("FloodWaitError escaped main — sleeping %ds then clean exit", secs)
        time.sleep(secs)
        sys.exit(0)
    except Exception:
        log.exception("fatal error")
        sys.exit(1)
    finally:
        loop.close()


if __name__ == "__main__":
    run()
