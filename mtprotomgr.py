"""mtprotomgr.py — MTProto bulk jobs driven by the USERBOT, controlled from
the control bot:

  /massdlt <chat_id> <start_link> <end_link>   delete every message in range
  /massdlt_status / /massdlt_stop
  /forward <target> <source> <start_link> <end_link>
                                               copy (no forward tag) a range
  /forward_status / /forward_stop / /forward_resume
  /add <channel_id> @bot1 [@bot2 ...]          add bot(s) as admin with all rights

TELEGRAM-SAFE (mirrors the v25/v25.1 /replace design):
- paced: one action every MASS_DELETE_DELAY / FORWARD_DELAY + jitter, and
  deletes go out in CHUNKS of MASS_DELETE_CHUNK — a 2000-message range is
  deleted chunk-by-chunk with rests between chunks, never one giant call.
- FloodWaitError mid-run is SLEPT THROUGH in place and the SAME work retried
  (cap FLOOD cap BULK_MAX_FLOOD), so no error ever escapes to the user.
- while any job runs the SCRAPER auto-pauses (Telegram's flood bucket is
  ACCOUNT-WIDE — the scraper's sends share it) and auto-resumes after.
- /forward persists its cursor in MongoDB after every message, so a crash,
  a stop, or a flood can be picked up with /forward_resume."""
import asyncio
import logging
import random
import re
import time

from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import EditAdminRequest, GetParticipantRequest
from telethon.tl.types import (Channel, ChatAdminRights, ChannelParticipantAdmin,
                               ChannelParticipantCreator)
from telethon.errors import UserNotParticipantError

import db as DB
from config import (MASS_DELETE_CHUNK, MASS_DELETE_DELAY, FORWARD_DELAY,
                    FORWARD_SWITCH_EVERY,  # v44: userbot rotation cadence
                    FORWARD_BATCH,         # v44.1: media batch size
                    BULK_MAX_FLOOD, BULK_PROGRESS_EVERY, ADMIN_USER_ID)
from flow import state

log = logging.getLogger("mtprotomgr")

_TG_MSG_RE = re.compile(
    r"(?:https?://)?t\.me/(?:c/(\d+)|([A-Za-z0-9_]+))/(\d+)")


# --------------------------------------------------------------------------
# shared job state
# --------------------------------------------------------------------------

class _Job:
    def __init__(self, kind):
        self.kind = kind            # "massdlt" | "forward"
        self.task = None
        self.stop = False           # cooperative stop flag
        self.status = "idle"        # idle|running|stopping|done|stopped|failed
        self.processed = 0
        self.failed = 0
        self.floods = 0
        self.total = 0
        self.detail = ""
        self.started = None

    def progress_text(self):
        if self.status in ("idle",):
            return None
        mins = ((time.time() - self.started) / 60) if self.started else 0
        icon = {"running": "⏳", "stopping": "🛑", "done": "✅",
                "stopped": "⏹", "failed": "❌"}.get(self.status, "ℹ️")
        txt = (f"{icon} {self.kind} — {self.status}\n"
               f"   {self.detail}\n"
               f"   {self.processed}/{self.total} done"
               + (f", {self.failed} failed" if self.failed else "")
               + (f", {self.floods} flood wait(s) slept" if self.floods else "")
               + (f" — {mins:.1f} min" if self.started else ""))
        return txt


massdlt_job = _Job("massdlt")
forward_job = _Job("forward")


async def _sleep_flood(fe, job, status_msg=None):
    """Sleep through a FloodWaitError in place. Returns True if the SAME
    work should be retried, False if the wait exceeds the cap (counted as
    a failure instead of parking forever)."""
    secs = getattr(fe, "seconds", 60) or 60
    if secs <= BULK_MAX_FLOOD:
        job.floods += 1
        log.warning("%s: FloodWait %ds — sleeping in place", job.kind, secs)
        if status_msg is not None:
            try:
                await status_msg.edit(
                    f"⏳ Flood wait {secs}s from Telegram — sleeping it off "
                    f"({job.processed}/{job.total} so far)…")
            except Exception:
                pass
        await asyncio.sleep(secs + 5)
        return True
    return False


def _pace(delay):
    """Jittered pacing — human-like, never a fixed burst pattern."""
    return asyncio.sleep(delay + random.uniform(0, 1.5))


def _parse_link_or_id(raw):
    """A message link (t.me/c/<ch>/<msg> or t.me/<name>/<msg>) -> (chat_key, msg_id);
    a bare number -> (None, msg_id). chat_key is int (-100…), @name, or None."""
    raw = (raw or "").strip()
    m = _TG_MSG_RE.search(raw)
    if m:
        ch = int("-100" + m.group(1)) if m.group(1) else m.group(2)
        return ch, int(m.group(3))
    if raw.lstrip("-").isdigit():
        return None, int(raw)
    return None, None


def _same_chat_key(chat_key, chat_id):
    """True when a link's channel key matches the command's chat_id."""
    if chat_key is None:
        return True
    if isinstance(chat_key, int):
        return chat_key == chat_id
    return True   # @username links can't be int-compared; trust the user


async def _resolve_range(scrape_client, chat_id, start_link, end_link):
    """Validate the chat and both boundary messages. Returns
    (entity, start_id, end_id) with start_id <= end_id, or raises ValueError
    with a user-facing reason."""
    ent = await scrape_client.get_entity(chat_id)
    if not isinstance(ent, (Channel,)):
        raise ValueError(f"{chat_id} isn't a channel/group the userbot can manage.")
    ch_s, sid = _parse_link_or_id(start_link)
    ch_e, eid = _parse_link_or_id(end_link)
    if not sid or not eid:
        raise ValueError("Couldn't parse start/end. Use message links "
                         "(https://t.me/c/<channel>/<msg>) or message ids.")
    if not _same_chat_key(ch_s, chat_id) or not _same_chat_key(ch_e, chat_id):
        raise ValueError("Those links don't belong to the given chat_id.")
    if sid > eid:
        sid, eid = eid, sid   # tolerate reversed links
    return ent, sid, eid


async def _scraper_pause():
    """Auto-pause the scraper while a bulk job runs; returns True if it had
    been running (so the job can resume it after)."""
    was = state.started and not state.paused
    if was:
        state.paused = True
    return was


async def _scraper_resume(was, ev=None):
    if was:
        state.paused = False
        if ev is not None:
            try:
                await ev.reply("▶️ Scraper resumed — bulk job finished.")
            except Exception:
                pass


async def _alert_admin(client, text):
    if not ADMIN_USER_ID:
        return
    try:
        await client.send_message(ADMIN_USER_ID, text)
    except Exception as e:
        log.warning("admin alert failed: %s", e)


# --------------------------------------------------------------------------
# MASS DELETE — /massdlt <chat_id> <start_link> <end_link>
# --------------------------------------------------------------------------

async def massdlt_start(scrape_client, ev, chat_id, start_link, end_link):
    if massdlt_job.status == "running":
        await ev.reply("⚠️ A /massdlt run is already in progress — "
                       "/massdlt_status to watch, /massdlt_stop to stop it.")
        return
    try:
        ent, sid, eid = await _resolve_range(scrape_client, chat_id,
                                             start_link, end_link)
    except Exception as e:
        await ev.reply(f"⚠️ {e}")
        return

    status = await ev.reply(f"🔍 Scanning {chat_id} messages {sid}–{eid}…")

    # Phase 1: collect the real message ids in range (service gaps skipped).
    ids = []
    try:
        async for m in scrape_client.iter_messages(ent, min_id=sid - 1,
                                                   max_id=eid + 1, reverse=True):
            if m is not None:
                ids.append(m.id)
    except Exception as e:
        await status.edit(f"⚠️ Scan failed: {e}")
        return
    if not ids:
        await status.edit(f"✅ Nothing to delete in {chat_id} between {sid}–{eid}.")
        return

    massdlt_job.task = asyncio.ensure_future(
        _massdlt_worker(scrape_client, ev, ent, ids, status))


async def _massdlt_worker(client, ev, ent, ids, status):
    job = massdlt_job
    job.stop = False; job.status = "running"; job.failed = 0; job.floods = 0
    job.processed = 0; job.total = len(ids); job.started = time.time()
    job.detail = f"deleting in {getattr(ent, 'title', ent.id)}"
    was = await _scraper_pause()
    total = len(ids)
    eta = (total / MASS_DELETE_CHUNK) * MASS_DELETE_DELAY / 60
    try:
        await status.edit(
            f"🧹 Deleting {total} message(s) in {getattr(ent, 'title', ent.id)}.\n"
            f"Chunked {MASS_DELETE_CHUNK} at a time, ~{MASS_DELETE_DELAY}s apart "
            f"(Telegram-safe) — ETA ~{eta:.1f} min.\n"
            "Flood waits are slept through automatically.\n"
            "⏸ Scraper auto-pauses during the run. /massdlt_stop to stop.")
    except Exception:
        pass
    try:
        # Phase 2: delete in CHUNKS so a huge range never fires one giant call.
        for i in range(0, total, MASS_DELETE_CHUNK):
            if job.stop:
                break
            chunk = ids[i:i + MASS_DELETE_CHUNK]
            while True:
                try:
                    await client.delete_messages(ent, chunk)
                    job.processed += len(chunk)
                    break
                except FloodWaitError as fe:
                    if await _sleep_flood(fe, job, status):
                        continue
                    job.failed += len(chunk)
                    break
                except Exception as e:
                    log.warning("massdlt chunk failed: %s", e)
                    job.failed += len(chunk)
                    break
            if job.processed and job.processed % (MASS_DELETE_CHUNK * max(1, BULK_PROGRESS_EVERY // 2)) < MASS_DELETE_CHUNK:
                try:
                    await status.edit(job.progress_text())
                except Exception:
                    pass
            await _pace(MASS_DELETE_DELAY)
        job.status = "stopped" if job.stop else "done"
        verb = "Stopped" if job.stop else "Done"
        mins = (time.time() - job.started) / 60
        summary = (f"🧹 {verb} — deleted {job.processed}/{total} in "
                   f"{getattr(ent, 'title', ent.id)} ({mins:.1f} min)"
                   + (f", {job.failed} failed" if job.failed else "")
                   + (f", {job.floods} flood wait(s) slept" if job.floods else ""))
        try:
            await status.edit(summary)
        except Exception:
            pass
        await _alert_admin(client, summary)
    except Exception as e:
        job.status = "failed"
        log.exception("massdlt worker error")
        try:
            await status.edit(f"❌ massdlt failed: {e}")
        except Exception:
            pass
    finally:
        await _scraper_resume(was, ev)


# --------------------------------------------------------------------------
# FORWARD (copy, no tag) — /forward <target> <source> <start_link> <end_link>
# --------------------------------------------------------------------------

async def forward_start(scrape_client, ev, target, source, start_link, end_link,
                        sm=None):
    """v44: sm = SessionManager so the forward can rotate across ALL userbot
    accounts — a FloodWait hands the SAME message to the next rested account,
    and every FORWARD_SWITCH_EVERY messages the baton passes anyway."""
    if forward_job.status == "running":
        await ev.reply("⚠️ A /forward run is already in progress — "
                       "/forward_status to watch, /forward_stop to stop it.")
        return
    try:
        src_ent, sid, eid = await _resolve_range(scrape_client, source,
                                                 start_link, end_link)
    except Exception as e:
        await ev.reply(f"⚠️ source: {e}")
        return
    try:
        tgt_ent = await scrape_client.get_entity(target)
    except Exception as e:
        await ev.reply(f"⚠️ Can't access the destination {target} with the userbot: {e}")
        return
    if not isinstance(tgt_ent, Channel):
        await ev.reply(f"⚠️ Destination {target} isn't a channel.")
        return

    status = await ev.reply(f"🔍 Scanning {source} messages {sid}–{eid}…")
    msgs = []
    try:
        async for m in scrape_client.iter_messages(src_ent, min_id=sid - 1,
                                                   max_id=eid + 1, reverse=True):
            if m is not None:
                msgs.append(m)
    except Exception as e:
        await status.edit(f"⚠️ Scan failed: {e}")
        return
    if not msgs:
        await status.edit(f"✅ Nothing to forward in {source} between {sid}–{eid}.")
        return

    # persist a resumable cursor so /forward_resume can pick up after a stop/crash
    full_ids = [m.id for m in msgs]
    await DB.set_config("fwd_job", {"target": target, "source": source,
                                    "ids": full_ids, "pos": 0,
                                    "src_title": getattr(src_ent, "title", str(source))})
    forward_job.task = asyncio.ensure_future(
        _forward_worker(scrape_client, ev, src_ent, tgt_ent, msgs, status, 0,
                        full_ids=full_ids, target_key=target, source_key=source,
                        sm=sm))  # v44


async def forward_resume(scrape_client, ev, sm=None):  # v44: rotation on resume too
    saved = await DB.get_config("fwd_job")
    if not saved or not saved.get("ids"):
        await ev.reply("⚠️ No stopped/interrupted forward to resume — start one with /forward.")
        return
    if forward_job.status == "running":
        await ev.reply("⚠️ A /forward run is already in progress.")
        return
    pos = saved.get("pos", 0)
    ids = saved["ids"]
    if pos >= len(ids):
        await ev.reply("✅ That forward already completed — nothing left to resume.")
        await DB.set_config("fwd_job", None)
        return
    try:
        src_ent = await scrape_client.get_entity(saved["source"])
        tgt_ent = await scrape_client.get_entity(saved["target"])
    except Exception as e:
        await ev.reply(f"⚠️ Can't access source/destination now: {e}")
        return
    status = await ev.reply(
        f"🔁 Resuming forward {saved['source']} → {saved['target']} at {pos}/{len(ids)}…")
    # refetch the remaining messages in ONE range scan (not per-id calls)
    remaining = set(ids[pos:])
    msgs = []
    try:
        lo, hi = min(remaining), max(remaining)
        async for m in scrape_client.iter_messages(src_ent, min_id=lo - 1,
                                                   max_id=hi + 1, reverse=True):
            if m is not None and m.id in remaining:
                msgs.append(m)
    except Exception as e:
        await status.edit(f"⚠️ Refetch failed: {e}")
        return
    forward_job.task = asyncio.ensure_future(
        _forward_worker(scrape_client, ev, src_ent, tgt_ent, msgs, status, pos,
                        full_total=len(ids), full_ids=ids,
                        target_key=saved["target"], source_key=saved["source"],
                        sm=sm))  # v44


async def _forward_worker(client, ev, src_ent, tgt_ent, msgs, status, done_base,
                          full_total=None, full_ids=None,
                          target_key=None, source_key=None, sm=None):
    """v44: sm (SessionManager) enables ACCOUNT ROTATION —
    - every FORWARD_SWITCH_EVERY sent messages the job moves to the next
      userbot on its own (even without any flood), spreading the load;
    - a FloodWait parks THAT account for its full wait and immediately retries
      the SAME message on the next rested account (entities are re-resolved per
      account — access hashes are account-bound);
    - only when EVERY account is flood-parked does the job sleep for the
      shortest remaining wait, then continue.
    Single-session setups behave exactly as before (falls back to sleeping the
    flood through in place)."""
    import forwarder
    job = forward_job
    job.stop = False; job.status = "running"; job.failed = 0; job.floods = 0
    job.started = time.time()
    job.total = full_total if full_total is not None else len(msgs)
    job.processed = done_base
    job.detail = f"{getattr(src_ent, 'title', 'source')} → {getattr(tgt_ent, 'title', 'dest')}"
    was = await _scraper_pause()
    eta = len(msgs) * FORWARD_DELAY / 60
    try:
        await status.edit(
            f"📨 Forwarding {len(msgs)} message(s) "
            f"({getattr(src_ent, 'title', 'src')} → {getattr(tgt_ent, 'title', 'dst')}).\n"
            f"One every ~{FORWARD_DELAY}s (Telegram-safe) — ETA ~{eta:.1f} min.\n"
            "Copied by reference — no 'Forwarded from' tag, zero download.\n"
            "Flood waits slept through. ⏸ Scraper auto-pauses. "
            "/forward_stop to stop, /forward_resume to continue.")
    except Exception:
        pass
    # v44: rotation state
    fwd_clients = sm.all() if sm else [client]
    fwd_n = len(fwd_clients)
    fwd_idx = 0
    fwd_parked = {}        # account index -> unix ts until which it rests
    fwd_since_switch = 0   # messages sent by the current account

    async def _fwd_rotate(reason):
        nonlocal client, src_ent, tgt_ent, fwd_idx, fwd_since_switch
        if fwd_n <= 1:
            return False
        now = time.time()
        free = [i for i in range(fwd_n) if fwd_parked.get(i, 0) <= now]
        if not free:
            # everyone is resting — sleep the shortest wait, then all are free
            wait = max(5, min(fwd_parked.values()) - now)
            job.floods += 1
            log.warning("forward: ALL %d accounts flood-parked — sleeping %ds",
                        fwd_n, wait)
            try:
                await status.edit(f"⏳ All {fwd_n} userbots hit flood limits — "
                                  f"resting {int(wait)}s, then continuing…")
            except Exception:
                pass
            await asyncio.sleep(wait)
            free = list(range(fwd_n))
        fwd_idx = free[0]
        client = fwd_clients[fwd_idx]
        fwd_since_switch = 0
        # v44.1: entity objects are ACCOUNT-BOUND (access hashes) — acc2 could
        # not send into entities resolved by acc1 ("Invalid channel object,
        # caused by SendMediaRequest"). Re-resolve BOTH channels for the new
        # account immediately after every rotation.
        try:
            src_ent = await client.get_entity(source_key)
            tgt_ent = await client.get_entity(target_key)
        except Exception as e:
            log.warning("forward: entity rebind on account %d failed: %s",
                        fwd_idx + 1, e)
        log.info("forward ROTATE (%s) -> account %d/%d (entities re-resolved)",
                 reason, fwd_idx + 1, fwd_n)
        try:
            await status.edit(f"🔄 Forward rotated to userbot {fwd_idx + 1}/{fwd_n} "
                              f"({reason}) — {job.processed}/{job.total} done.")
        except Exception:
            pass
        return True

    async def _fwd_save_pos():
        if full_ids is not None:
            await DB.set_config("fwd_job", {
                "target": target_key, "source": source_key,
                "ids": full_ids, "pos": job.processed,
                "src_title": getattr(src_ent, "title", "")})

    try:
        i = 0
        while i < len(msgs):
            if job.stop:
                break
            if fwd_n > 1 and fwd_since_switch >= FORWARD_SWITCH_EVERY:
                fwd_parked[fwd_idx] = time.time() + FORWARD_SWITCH_EVERY * FORWARD_DELAY
                await _fwd_rotate(f"{FORWARD_SWITCH_EVERY} messages done")
            m = msgs[i]
            if getattr(m, "media", None) is not None:
                # v44.1: BATCH consecutive media (up to FORWARD_BATCH) into ONE
                # group send; one pace per batch instead of per message.
                group, j = [], i
                while (j < len(msgs) and len(group) < FORWARD_BATCH
                       and getattr(msgs[j], "media", None) is not None):
                    group.append(msgs[j])
                    j += 1
                # v44.1: file references are ACCOUNT-BOUND — refetch the batch
                # with the CURRENT account so its media is sendable by it
                try:
                    fresh = await client.get_messages(src_ent,
                                                      ids=[g.id for g in group])
                    group = [g for g in fresh if g] or group
                except Exception as e:
                    log.warning("forward: batch refetch failed (%s) — using cached", e)
                while True:
                    try:
                        await forwarder.send_media(client, src_ent, group, tgt_ent)
                        job.processed += len(group)
                        fwd_since_switch += len(group)
                        await _fwd_save_pos()
                        break
                    except FloodWaitError as fe:
                        fwd_parked[fwd_idx] = time.time() + fe.seconds
                        if fwd_n > 1 and await _fwd_rotate(f"FloodWait {fe.seconds}s"):
                            try:
                                fresh = await client.get_messages(
                                    src_ent, ids=[g.id for g in group])
                                group = [g for g in fresh if g] or group
                            except Exception:
                                pass
                            continue
                        if await _sleep_flood(fe, job, status):
                            continue
                        job.failed += len(group)
                        job.processed += len(group)
                        break
                    except Exception as e:
                        log.warning("forward batch %s failed: %s",
                                    [g.id for g in group], e)
                        job.failed += len(group)
                        job.processed += len(group)
                        break
                i = j
            else:
                while True:
                    try:
                        if (m.text or "").strip():
                            await client.send_message(tgt_ent, m.message,
                                                      buttons=getattr(m, "buttons", None))
                        job.processed += 1
                        fwd_since_switch += 1
                        await _fwd_save_pos()
                        break
                    except FloodWaitError as fe:
                        fwd_parked[fwd_idx] = time.time() + fe.seconds
                        if fwd_n > 1 and await _fwd_rotate(f"FloodWait {fe.seconds}s"):
                            continue
                        if await _sleep_flood(fe, job, status):
                            continue
                        job.failed += 1
                        job.processed += 1
                        break
                    except Exception as e:
                        log.warning("forward msg %s failed: %s", getattr(m, "id", "?"), e)
                        job.failed += 1
                        job.processed += 1
                        break
                i += 1
            await _pace(FORWARD_DELAY)
        job.status = "stopped" if job.stop else "done"
        verb = "Stopped" if job.stop else "Done"
        mins = (time.time() - job.started) / 60
        summary = (f"📨 {verb} — forwarded {job.processed}/{job.total} "
                   f"({getattr(src_ent, 'title', 'src')} → {getattr(tgt_ent, 'title', 'dst')}) "
                   f"in {mins:.1f} min"
                   + (f", {job.failed} failed" if job.failed else "")
                   + (f", {job.floods} flood wait(s) slept" if job.floods else ""))
        if job.status == "done":
            await DB.set_config("fwd_job", None)   # clear the resumable cursor
            summary += "\nCursor cleared."
        else:
            summary += "\n/forward_resume to continue from where it stopped."
        try:
            await status.edit(summary)
        except Exception:
            pass
        await _alert_admin(client, summary)
    except Exception as e:
        job.status = "failed"
        log.exception("forward worker error")
        try:
            await status.edit(f"❌ forward failed: {e}\n/forward_resume to retry.")
        except Exception:
            pass
    finally:
        await _scraper_resume(was, ev)


# --------------------------------------------------------------------------
# ADD BOT AS ADMIN — /add <channel_id> @bot1 [@bot2 ...]
# --------------------------------------------------------------------------

# Right keys — (attribute, human label, applies to megagroups too?).
# manage_call & other are GROUP-only rights: setting them in a broadcast
# channel triggers Telegram's "some rights only apply to channels and vice
# versa" error, so they're only ever granted in megagroups.
_RIGHT_KEYS = [
    ("post_messages", "post", False),
    ("edit_messages", "edit", False),
    ("delete_messages", "delete", True),
    ("ban_users", "ban", True),
    ("invite_users", "invite", True),
    ("pin_messages", "pin", True),
    ("add_admins", "add_admins", True),
    ("manage_call", "manage_call", True),   # group-only — filtered for channels
    ("other", "other", True),               # group-only — filtered for channels
]


async def _invoker_rights(client, chan):
    """The USERBOT's own admin rights in this channel. Telegram refuses to let
    an admin grant a right it doesn't itself hold, so this is the ceiling.
    Creator => all rights. Returns a dict attr->bool."""
    try:
        me = await client.get_me()
        part = await client(GetParticipantRequest(chan, me.id))
        pr = part.participant
        if isinstance(pr, ChannelParticipantCreator):
            return {k: True for k, _, _ in _RIGHT_KEYS}
        if isinstance(pr, ChannelParticipantAdmin):
            ar = pr.admin_rights
            return {k: bool(getattr(ar, k, False)) for k, _, _ in _RIGHT_KEYS}
        return {k: False for k, _, _ in _RIGHT_KEYS}
    except UserNotParticipantError:
        return {k: False for k, _, _ in _RIGHT_KEYS}
    except Exception:
        # unknown -> assume a reasonable admin ceiling, the retry fallback
        # trims further if Telegram still objects
        return {k: True for k, _, _ in _RIGHT_KEYS if k not in ("manage_call",)}


def _make_rights(caps, megagroup):
    """Build a ChatAdminRights from the capabilities the invoker can pass.
    Group-only rights are dropped in broadcast channels."""
    kw = {}
    for attr, _label, group_ok in _RIGHT_KEYS:
        if attr in ("manage_call", "other") and not megagroup:
            kw[attr] = False
        else:
            kw[attr] = caps.get(attr, False)
    kw["anonymous"] = False
    return ChatAdminRights(**kw)


def _caps_label(rights):
    names = [lbl for attr, lbl, _ in _RIGHT_KEYS if getattr(rights, attr, False)]
    return ", ".join(names) if names else "none"


async def add_bots(scrape_client, ev, channel, botnames):
    if not botnames:
        await ev.reply("Usage: /add <channel id> @bot1 [@bot2 @bot3 …]\n"
                       "Adds each bot to the channel as admin with as many rights as "
                       "the USERBOT can grant (it mirrors the userbot's own rights — "
                       "Telegram won't let it pass rights it doesn't have).")
        return
    try:
        chan = await scrape_client.get_entity(channel)
    except Exception as e:
        await ev.reply(f"⚠️ Can't access channel {channel} with the userbot: {e}")
        return
    if not isinstance(chan, Channel):
        await ev.reply(f"⚠️ {channel} isn't a channel.")
        return

    megagroup = bool(getattr(chan, "megagroup", False))
    caps = await _invoker_rights(scrape_client, chan)
    rights = _make_rights(caps, megagroup)
    # channel-safe minimal fallback if the full set is still rejected
    safe = _make_rights({k: caps.get(k, False) for k in
                         ("post_messages", "edit_messages", "delete_messages",
                          "invite_users")}, False)

    flood_job = _Job("add")   # local counter for flood waits
    granted = _caps_label(rights)
    lines = [f"🤖 Adding {len(botnames)} bot(s) as admin in "
             f"**{getattr(chan, 'title', channel)}** "
             f"({'group' if megagroup else 'channel'}) — granting: {granted}"]
    for name in botnames:
        uname = name.strip().lstrip("@")
        if not uname:
            continue
        try:
            b = await scrape_client.get_entity(uname)
            if not getattr(b, "bot", False):
                lines.append(f"  ⚠️ @{uname} is not a bot — skipped.")
                continue
            done = False
            for attempt_rights, tag in ((rights, ""), (safe, " (reduced rights)")):
                while True:
                    try:
                        await scrape_client(EditAdminRequest(chan, b, attempt_rights, rank=""))
                        lines.append(f"  ✅ @{uname} — added as admin{tag} [{_caps_label(attempt_rights)}]")
                        done = True
                        break
                    except FloodWaitError as fe:
                        if await _sleep_flood(fe, flood_job):
                            continue
                        lines.append(f"  ❌ @{uname} — flood wait too long")
                        done = True
                        break
                    except Exception as e:
                        if attempt_rights is rights:
                            log.warning("add @%s full rights rejected (%s) — retrying reduced", uname, e)
                            break   # fall through to the reduced-rights attempt
                        lines.append(f"  ❌ @{uname} — {e}")
                        done = True
                        break
                if done:
                    break
        except Exception as e:
            lines.append(f"  ❌ @{uname} — {e}")
        await _pace(2.0)   # gentle pacing between invites
    await ev.reply("\n".join(lines))
