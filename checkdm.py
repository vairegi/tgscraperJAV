"""checkdm.py — /checkdm on|off pipeline.

When enabled, the USERBOT watches its DM with @richmining for channel invite
links (public t.me/<name> or private t.me/+<hash> / t.me/joinchat/<hash>).
For each link it:
  1. joins the channel,
  2. WAITS until @richmining promotes the userbot to admin (polls),
  3. adds @lifesimplerbot as admin with EXACTLY the rights the userbot holds
     there (Telegram refuses to grant more than the invoker has — rights are
     mirrored via mtprotomgr's _invoker_rights/_make_rights, group-only
     rights auto-dropped in broadcast channels, one reduced-rights retry),
  4. leaves the channel,
  5. replies to the link message: DONE ✅ + the invite link,
then waits for the next link. Several links are handled concurrently
(capped), each link processed once (dedupe across sessions).

Telegram-safe: joins are paced, FloodWait is slept through in place, and a
channel where no admin rights arrive within ADMIN_WAIT_TIMEOUT is left again
with a warning DM to @richmining."""
import asyncio
import logging
import random
import re
import time

from telethon import events
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.channels import (EditAdminRequest, JoinChannelRequest,
                                            LeaveChannelRequest)
from telethon.tl.functions.messages import ImportChatInviteRequest

import db as DB
from mtprotomgr import _invoker_rights, _make_rights, _caps_label

log = logging.getLogger("checkdm")

WATCH_USER = "richmining"      # only DMs from this username are acted on
GRANT_BOT = "lifesimplerbot"   # the bot to promote in each joined channel
MAX_JOBS = 5                   # concurrent channels being processed
ADMIN_WAIT_TIMEOUT = 900       # max seconds waiting for the admin promotion
ADMIN_WAIT_POLL = 5            # seconds between admin-rights checks

# public username, private +hash, legacy joinchat/hash
_INVITE_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.(?:me|dog))/"
    r"(\+[^\s]+|joinchat/[^\s]+|[A-Za-z0-9_]{4,})")


def _parse_invite(text):
    """First Telegram invite/channel link in the text -> (kind, token).
    kind is 'username' or 'hash'. Returns (None, None) when no link."""
    m = _INVITE_RE.search(text or "")
    if not m:
        return None, None
    token = m.group(1).rstrip("/").strip()
    if token.startswith("joinchat/"):
        return "hash", token[len("joinchat/"):]
    if token.startswith("+"):
        return "hash", token[1:]
    return "username", token


_active = set()              # "kind:token" keys currently being processed
_sema = asyncio.Semaphore(MAX_JOBS)


async def _join(client, kind, token):
    """Join the channel behind the invite -> channel entity."""
    if kind == "username":
        chan = await client.get_entity(token)
        try:
            await client(JoinChannelRequest(chan))
        except UserAlreadyParticipantError:
            pass
        return chan
    # private invite hash
    upd = await client(ImportChatInviteRequest(token))
    chats = getattr(upd, "chats", None) or []
    if not chats:
        raise RuntimeError("invite accepted but no channel returned")
    return chats[0]


async def _wait_for_admin(client, chan):
    """Poll until the userbot holds ANY admin rights in chan (the promotion
    from @richmining). Returns the rights dict, or None on timeout."""
    deadline = time.time() + ADMIN_WAIT_TIMEOUT
    while time.time() < deadline:
        caps = await _invoker_rights(client, chan)
        if any(caps.values()):
            return caps
        await asyncio.sleep(ADMIN_WAIT_POLL + random.uniform(0, 2))
    return None


async def _grant_bot_admin(client, chan, caps):
    """Add GRANT_BOT as admin with the rights the userbot holds (never more).
    One reduced-rights retry if Telegram still rejects the full mirror.
    Returns (rights_used, error_or_None)."""
    bot_ent = await client.get_entity(GRANT_BOT)
    if not getattr(bot_ent, "bot", False):
        return None, f"@{GRANT_BOT} did not resolve to a bot"
    megagroup = bool(getattr(chan, "megagroup", False))
    full = _make_rights(caps, megagroup)
    reduced = _make_rights({k: caps.get(k, False) for k in
                            ("post_messages", "edit_messages",
                             "delete_messages", "invite_users")}, False)
    last_err = None
    for attempt in (full, reduced):
        while True:
            try:
                await client(EditAdminRequest(chan, bot_ent, attempt, rank=""))
                return attempt, None
            except FloodWaitError as fe:
                secs = min(getattr(fe, "seconds", 60), 900)
                log.warning("checkdm: FloodWait %ds promoting bot — sleeping", secs)
                await asyncio.sleep(secs + 5)
                continue
            except Exception as e:
                last_err = e
                log.warning("checkdm: grant attempt rejected (%s)%s",
                            e, " — retrying reduced rights" if attempt is full else "")
                break   # next attempt (or give up after reduced)
    return None, last_err


async def _do_job(client, message, kind, token, link_text):
    key = f"{kind}:{token}"
    if key in _active:
        return                       # same link already being handled
    _active.add(key)
    async with _sema:
        chan = None
        try:
            chan = await _join(client, kind, token)
            title = getattr(chan, "title", token)
            log.info("checkdm: joined %s (%s)", title, key)
            caps = await _wait_for_admin(client, chan)
            if caps is None:
                await message.reply(
                    f"⚠️ {link_text}\nJoined **{title}** but no admin rights arrived "
                    f"within {ADMIN_WAIT_TIMEOUT // 60} min — leaving the channel.")
                try:
                    await client(LeaveChannelRequest(chan))
                except Exception:
                    pass
                return
            rights, err = await _grant_bot_admin(client, chan, caps)
            if err is not None:
                await message.reply(
                    f"⚠️ {link_text}\nIn **{title}** but could not add @{GRANT_BOT}: {err}\n"
                    "(Does the userbot have the add-admins right there?) Leaving.")
                try:
                    await client(LeaveChannelRequest(chan))
                except Exception:
                    pass
                return
            try:
                await client(LeaveChannelRequest(chan))
            except Exception as e:
                log.warning("checkdm: leave failed for %s: %s", key, e)
            await message.reply(
                f"✅ DONE — {link_text}\n"
                f"@{GRANT_BOT} is now admin in **{title}** "
                f"[{_caps_label(rights)}] — userbot left the channel. "
                "Ready for the next link.")
            log.info("checkdm: DONE %s -> %s [%s]", key, title, _caps_label(rights))
        except Exception as e:
            log.exception("checkdm: job failed for %s", key)
            try:
                await message.reply(f"❌ {link_text}\nFailed: {e}")
            except Exception:
                pass
            if chan is not None:
                try:
                    await client(LeaveChannelRequest(chan))
                except Exception:
                    pass
        finally:
            _active.discard(key)


def register(client):
    """Attach the DM watcher to a userbot session. Gated by the Mongo-backed
    checkdm_enabled flag (/checkdm on|off in the control bot)."""

    @client.on(events.NewMessage())
    async def _watch(ev):
        try:
            if not await DB.get_config("checkdm_enabled"):
                return
        except Exception:
            return
        # DMs only — never channels/groups
        if getattr(ev, "is_group", False) or getattr(ev, "is_channel", False):
            return
        try:
            sender = await ev.get_sender()
        except Exception:
            return
        uname = (getattr(sender, "username", None) or "").lower()
        if uname != WATCH_USER:
            return
        kind, token = _parse_invite(ev.raw_text or "")
        if not kind:
            return
        log.info("checkdm: invite from @%s -> %s:%s", uname, kind, token)
        asyncio.ensure_future(_do_job(client, ev.message, kind, token,
                                      (ev.raw_text or "").strip()))
