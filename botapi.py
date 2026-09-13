"""botapi.py — control BOT (BOT_TOKEN from BotFather) with a tappable menu.
v13: per-target DB channels. /target wizard now asks for the channel id AND
then its DB channel; /setdb adjusts a target's DB later; /reset and /goto are
per-target; all wizards accept inline args (/adddb -100…); /help lists every
command. Only ADMIN_USER_ID can use it."""
from telethon import TelegramClient, events
from telethon.sessions import MemorySession
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN_USER_ID, BTN_SHORT_LINK
import re
import db as DB
from flow import state
from telethon.tl.types import Channel, Chat, User

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
    ("linkbutton", "List or add LINK_BOT button labels (no restart)"),
    ("removelinkbutton", "Remove a LINK_BOT button label by number"),
    ("goto",     "Set a target's start message (/goto <n> <msg> or link)"),
    ("reset",    "Reset a target's progress to post 1"),
    ("lastpost", "Newest post in a target channel"),
    ("start",    "Start scraping"),
    ("pause",    "Pause all, or one target: /pause 2"),
    ("resume",   "Resume all, or one target: /resume 2"),
    ("status",   "Live stage & config"),
    ("current",  "Current post & stage"),
    ("progress", "Stats, last post, failure reasons"),
    ("skip",     "Skip current post"),
    ("stop",     "Stop the scraper"),
    ("cancel",   "Cancel an active wizard prompt"),
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
                     "/pause 2 pauses ONLY target 2 (see /targets for numbers), "
                     "/resume 2 resumes it — bare /pause //resume affects ALL targets. "
                     "When LINK_BOT renames its button: /linkbutton <new text> — "
                     "active instantly, no restart. "
                     "Caught-up channels re-scan for new posts every 30s.")
        await ev.reply("\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"^/ping$"))
    async def ping_cmd(ev):
        if _admin(ev.sender_id):
            await ev.reply(f"🏓 Pong — control bot + scraper alive.\n"
                           f"📍 Stage: {state.stage} | Running: {state.running} | Paused: {state.paused}")

    # ---------- LINK_BOT button labels (Mongo-backed, no restart) ----------
    @bot.on(events.NewMessage(pattern=r"^/linkbutton(?:\s+(.+))?$"))
    async def linkbutton_cmd(ev):
        if not _admin(ev.sender_id):
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
        if not _admin(ev.sender_id):
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
        if not _admin(ev.sender_id):
            return
        arg = (ev.pattern_match.group(1) or "").strip()
        if arg:
            _pending[ev.sender_id] = ("target_db", _parse_chat_id(arg))
            await ev.reply(f"Target: **{_parse_chat_id(arg)}**\nNow send the DB channel id for THIS target "
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
            await _save_simple(ev, field, _parse_chat_id(arg))
            return
        _pending[ev.sender_id] = (field, None)
        label = "bypass group" if field == "bypass_id" else "fallback DB channel"
        await ev.reply(f"Send me the {label} id (numeric like -100… or @username).\nCancel: /cancel")

    async def _save_simple(ev, field, v):
        try:
            ent = await scrape_client.get_entity(v)
        except Exception as e:
            await ev.reply(f"⚠️ Can't access that chat with the userbot account: {e}")
            return
        if field == "db_id" and not _entity_ok(ent, "channel"):
            await ev.reply(_not_a_channel_msg(v, "channel"))
            return
        if field == "bypass_id" and not _entity_ok(ent, "group"):
            await ev.reply(_not_a_channel_msg(v, "group"))
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
        if not item or not _admin(ev.sender_id) or ev.raw_text.startswith("/"):
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
        await _save_simple(ev, kind, _parse_chat_id(ev.raw_text))

    # ---------- view ----------
    @bot.on(events.NewMessage(pattern=r"^/targets$"))
    async def targets_cmd(ev):
        if not _admin(ev.sender_id):
            return
        listing = await _list_targets_text()
        if not listing:
            await ev.reply("No target channels. Add one with /target")
            return
        await ev.reply(listing + "\n\n/setdb to change a DB, /deltarget to remove, "
                                 "/pause <n> //resume <n> to pause/resume one target.")

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

    @bot.on(events.NewMessage(pattern=r"^/(pause|resume)(?:\s+(\d+))?$"))
    async def pause_cmd(ev):
        if not _admin(ev.sender_id):
            return
        action = ev.pattern_match.group(1)
        n = ev.pattern_match.group(2)
        if action == "pause":
            if n is None:
                # bare /pause — global pause (unchanged behavior)
                state.paused = True
                await ev.reply("⏸ Paused. Progress is saved in MongoDB — safe even if "
                               "Render crashes. /resume to continue.")
                return
            targets = await DB.get_targets()
            if not (1 <= int(n) <= len(targets)):
                await ev.reply((await _list_targets_text() or "No targets set.") +
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
        if n is None:
            # bare /resume — resume EVERYTHING: global flag + all per-target flags
            state.paused = False; state.abort = False; state.started = True
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
        targets = await DB.get_targets()
        if not (1 <= int(n) <= len(targets)):
            await ev.reply((await _list_targets_text() or "No targets set.") +
                           f"\n\n⚠️ Target number must be 1-{len(targets)} (see /targets).")
            return
        t = targets[int(n) - 1]
        await DB.set_target_paused(t["id"], False)
        state.paused_ids.discard(t["id"])
        state.paused = False; state.abort = False; state.started = True
        state.reset_gen += 1; state._last_scan = None
        rp = await DB.get_progress(t["id"])
        await ev.reply(f"▶️ Target {n} ({t['id']}) resumed — continues from message id {rp}.")

    @bot.on(events.NewMessage(pattern=r"^/(status|current)$"))
    async def status_cmd(ev):
        if _admin(ev.sender_id):
            cfg = await DB.get_config()
            listing = await _list_targets_text() or "  (none)"
            ind = sorted(str(i + 1) for i, t in enumerate(await DB.get_targets()) if t.get("paused"))
            await ev.reply(f"🤖 Running: {state.running} | Paused: {state.paused}"
                           + (f" | Individually paused targets: {', '.join(ind)}" if ind else "") + "\n"
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
        state.reset_gen += 1
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
        state.reset_gen += 1
        state._last_scan = None
        await ev.reply(f"📌 Target {tid} will resume from message {mid}. /start or /resume to go.")


async def start(scrape_client):
    global bot
    bot = TelegramClient(MemorySession(), API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    register(scrape_client)
    await _menu()
    return bot
