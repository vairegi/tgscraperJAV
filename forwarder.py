"""forwarder.py — send cover post + media to the DB channel.
v17: copy-mode BY REFERENCE — no download, no temp files, no disk I/O.
v21: DELIVERY VERIFICATION + full-dump support.

THE BUG THIS FIXES: Telegram sometimes ACCEPTS a by-reference send whose
file reference is already dead server-side — send_file returns normally,
no exception, but the created message carries NO media, so it never
renders in the DB channel. That's exactly how Hilda's 3 videos "sent OK"
(log said 'post 163 done') yet never arrived, while photos/decorations
(live references) landed fine. v17 had no way to notice.

Now every media send is VERIFIED: the returned message must actually carry
media. An empty result is treated like a stale reference: the empty shell
message is deleted from the DB channel, the source message is refetched
from its chat (fresh file reference) and the send retried. Every delivered
message logs its DB-channel id so delivery can be checked by eye.

Also: text-only messages are delivered as text (owner wants EVERYTHING the
media bot sends — videos, stickers, photos, texts — mirrored to the DB).

Still zero downloads, zero temp files, no "Forwarded from" tag, and all
video attributes (duration, thumbnail, filename, streaming, spoiler) are
preserved because the same server-side file reference is reused.

v60: CONTENT-PROTECTED targets (Telegram "Restrict saving content" /
noforwards) refuse server-side copies — ChatForwardsRestrictedError. Their
files are DOWNLOADED with the userbot and RE-UPLOADED into the DB channel as
brand-new messages (same caption/buttons/spoiler; a whole ALBUM travels as one
album again), temp files deleted at once. Detected two ways: the per-message
noforwards flag AND the error itself — protection can be switched on AFTER a
post was published, which leaves the per-message flag unset (exactly what
killed posts 11/12/21/22 in the render log). Once a target refuses a copy it is
remembered, so every later post on it goes straight to download + re-upload."""
import asyncio
import logging
import mimetypes
import os
import tempfile

from telethon.errors import (FileReferenceExpiredError, MediaInvalidError,
                             MediaEmptyError,
                             ChatForwardsRestrictedError)  # v60: protected-chat copy refusal

log = logging.getLogger("forwarder")

SEND_RETRIES = 3
RETRY_DELAY = 5  # seconds between send attempts
TMP_PREFIX = "tgscraper_protected_"  # v60: temp files for downloaded media

# v39: per-DB-channel delivery locks. With parallel multi-userbot scraping,
# several workers can finish collecting at the same time — this lock
# guarantees one post's complete bundle (cover FIRST, then ALL its media)
# lands in the DB channel before the next post's bundle begins, so posts
# never interleave. Keyed by DB channel id; created lazily on the running
# event loop (Py3.14 has no loop at import time).
_LOCKS = {}

# v60: targets that refused a server-side copy (content protection). The first
# ChatForwardsRestrictedError adds the target here, so every later post on it
# goes straight to download + re-upload instead of a doomed by-reference send.
_PROTECTED = set()


def delivery_lock(dbc):
    key = str(dbc)
    if key not in _LOCKS:
        _LOCKS[key] = asyncio.Lock()
    return _LOCKS[key]


class StaleRef(Exception):
    """Raised when Telegram accepted a send but the new message has no
    media — the file reference was dead server-side. Treated exactly like
    FileReferenceExpiredError: refetch the source message and retry."""
    pass


def _spoiler(msg):
    return bool(getattr(getattr(msg, "media", None), "spoiler", False))


def _file_ext(msg):
    """v60: temp-file extension for a media message — photos are jpeg, a
    document keeps its own mime type, else its original file name."""
    doc = getattr(msg, "document", None)
    if doc is None:
        return ".jpg"
    mt = (getattr(doc, "mime_type", "") or "").split(";")[0].strip()
    if mt:
        ext = mimetypes.guess_extension(mt)
        if ext:
            return ext
    for attr in (getattr(doc, "attributes", None) or []):
        fn = getattr(attr, "file_name", None)
        if fn:
            ext = os.path.splitext(fn)[1]
            if ext:
                return ext
    return ""


async def _album_group(client, msg, source):
    """v60: a cover post can be an ALBUM. If this message carries a
    grouped_id, pull its media siblings so the WHOLE cover travels together.
    Falls back to [msg] when it isn't an album or the siblings can't be read."""
    gid = getattr(msg, "grouped_id", None)
    if not gid or source is None:
        return [msg]
    try:
        ids = list(range(max(1, msg.id - 9), msg.id + 10))
        raw = await client.get_messages(source, ids=ids)
        sibs = [m for m in (raw or [])
                if m is not None and getattr(m, "grouped_id", None) == gid
                and getattr(m, "media", None) is not None]
    except Exception:
        return [msg]
    if len(sibs) <= 1:
        return [msg]
    sibs.sort(key=lambda m: m.id)
    return sibs


async def _download_one(client, msg, source):
    """v60: download ONE protected media message to a fresh temp file.
    A dead file reference is refetched once and retried. Returns (path, tmp)."""
    tmp = tempfile.mktemp(prefix=TMP_PREFIX, suffix=_file_ext(msg))
    got = None
    for attempt in (1, 2):
        try:
            got = await client.download_media(msg, file=tmp)
            break
        except FileReferenceExpiredError:
            if attempt == 2 or source is None:
                raise
            fresh = await _refetch(client, msg, source)
            if fresh is None:
                raise
            msg = fresh
    if not got or not os.path.exists(tmp):
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError("protected media download returned nothing")
    return got, tmp


async def download_and_reupload(client, dbc, msg, caption=None, buttons=None,
                                source=None):
    """v60: PROTECTED-CONTENT path. The source chat forbids server-side copies
    (Telegram 'Restrict saving content' / noforwards), so the file(s) are
    DOWNLOADED with THIS account and RE-UPLOADED to the DB channel as a
    brand-new message: same caption, same buttons, same spoiler flag; a single
    document keeps its attributes + mime type, and an album stays an album. The
    temp files are deleted in `finally` — on success AND on failure — so
    Render's small disk never fills up."""
    if caption is None:
        caption = msg.message or ""
    if getattr(msg, "media", None) is None:
        return await _send_one(client, dbc, msg, source, caption=caption,
                               buttons=buttons)
    group = await _album_group(client, msg, source)
    tmp_files = []
    try:
        paths = []
        for m in group:
            got, tmp = await _download_one(client, m, source)
            tmp_files.append(tmp)
            paths.append(got)
        doc = getattr(group[0], "document", None)
        kwargs = {"caption": caption, "buttons": buttons, "spoiler": _spoiler(group[0])}
        if doc is not None and len(paths) == 1:
            attrs = getattr(doc, "attributes", None)
            if attrs:
                kwargs["attributes"] = attrs
            mt = (getattr(doc, "mime_type", "") or "").split(";")[0].strip()
            if mt:
                kwargs["mime_type"] = mt
        r = await client.send_file(dbc, paths[0] if len(paths) == 1 else paths,
                                   **kwargs)
        out = list(r) if isinstance(r, (list, tuple)) else [r]
        empties = [x for x in out if not getattr(x, "media", None)]
        for x in empties:
            try:
                await x.delete()
            except Exception:
                pass
        if empties:
            raise RuntimeError("protected re-upload produced an empty message")
        ids = ", ".join(str(getattr(x, "id", "?")) for x in out)
        log.info("protected content: msg %s (%d file(s)) downloaded + re-uploaded"
                 " -> DB msg %s", getattr(msg, "id", "?"), len(paths), ids)
        return out[0]
    finally:
        for t in tmp_files:
            try:
                if os.path.exists(t):
                    os.remove(t)
            except OSError:
                pass


async def _refetch(client, msg, source):
    """Pull a fresh copy of the message from its source chat -> fresh
    file_reference. Returns the fresh message or None."""
    try:
        fresh = await client.get_messages(source, ids=msg.id)
    except Exception:
        return None
    if not fresh or not getattr(fresh, "media", None):
        return None
    return fresh


async def _send_one(client, dbc, msg, source, caption=None, buttons=None):
    """Re-send ONE message into the DB channel by reference, VERIFIED.

    msg.media carries the Telegram-side file reference, so the file is
    copied server-to-server: nothing is downloaded to the Render disk and
    nothing is re-uploaded. No forward tag, because this is a fresh
    send_file — not forward_messages.

    A send whose result carries no media is a silently-dead reference:
    the empty shell is deleted and the source message refetched for a
    fresh reference, then retried (up to SEND_RETRIES attempts)."""
    if caption is None:
        caption = msg.message or ""
    media = getattr(msg, "media", None)
    if media is None:
        # text-only message — deliver the text so NOTHING the media bot
        # sent is skipped (e.g. the bot's notes/warnings/labels)
        if caption:
            r = await client.send_message(dbc, caption, buttons=buttons)
            log.info("delivered text-only msg %s -> DB msg %s",
                     getattr(msg, "id", "?"), getattr(r, "id", "?"))
        return
    last_err = None
    for attempt in range(1, SEND_RETRIES + 1):
        try:
            r = await client.send_file(dbc, media, caption=caption,
                                       buttons=buttons, spoiler=_spoiler(msg))
            if not getattr(r, "media", None):
                # send "succeeded" but produced an EMPTY message -> dead
                # reference. Remove the shell so the DB channel stays clean,
                # then go through the refetch path below.
                try:
                    await r.delete()
                except Exception:
                    pass
                log.warning("send of msg %s produced an EMPTY message "
                            "(dead file reference) — refetching",
                            getattr(msg, "id", "?"))
                raise StaleRef()
            log.info("delivered msg %s -> DB msg %s",
                     getattr(msg, "id", "?"), getattr(r, "id", "?"))
            return
        except (FileReferenceExpiredError, StaleRef, MediaInvalidError,
                MediaEmptyError) as e:
            # stale/dead/foreign-account reference: refetch from the source
            # chat with THIS client -> fresh account-bound access_hash +
            # file_reference -> retry with the fresh media. v39.2: MediaEmptyError
            # must take this refetch path too — before, it fell into the generic
            # retry below and re-sent the SAME unusable object 3x, permanently
            # failing posts on any account that didn't fetch them itself.
            log.info("msg %s reference unusable (%s) — refetching",
                     getattr(msg, "id", "?"), type(e).__name__)
            last_err = e
            fresh = await _refetch(client, msg, source)
            if fresh is None:
                break  # refetch found nothing — give up with the error
            msg = fresh
            media = fresh.media
        except ChatForwardsRestrictedError:
            # v60: deterministic refusal — the source chat forbids server-side
            # copies. Retrying the identical send is pointless (the old code
            # burned 3 attempts x 5s here), so raise NOW and let the caller
            # switch to the download + re-upload path.
            raise
        except Exception as e:
            last_err = e
            log.warning("send attempt %d/%d failed for msg %s: %s",
                        attempt, SEND_RETRIES, getattr(msg, "id", "?"), e)
        if attempt < SEND_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    raise last_err


async def send_cover(client, target, msg, dbc):
    """Cover post from the target channel: same image (spoiler kept), same
    caption, same buttons — as a fresh message, no forward tag, no download.

    v42: RESTRICTED targets (msg.noforwards — the channel's 'Restrict saving
    content' flag) block copy-by-reference: the file reference can't be
    re-sent outside the channel. Workaround: DOWNLOAD the cover to a temp
    file, UPLOAD it to the DB channel as a brand-new message with the exact
    same caption/buttons, and DELETE the temp file immediately afterwards —
    on success AND on failure — so Render's small disk never fills up. The
    new DB post is a fresh userbot post (not restricted), so the DB2 clean
    mirror keeps working on it normally."""
    # v42/v60: content-protected target -> download + re-upload (a server-side
    # copy is forbidden). Proactive check on the per-message flag, plus the
    # _PROTECTED memo filled the moment a target refuses a copy.
    if getattr(msg, "media", None) is not None and (
            getattr(msg, "noforwards", False) or target in _PROTECTED):
        return await download_and_reupload(client, dbc, msg,
                                           caption=msg.message or "",
                                           buttons=msg.buttons, source=target)
    try:
        await _send_one(client, dbc, msg, target,
                        caption=msg.message or "", buttons=msg.buttons)
    except ChatForwardsRestrictedError:
        # v60: the per-message noforwards flag is MISSING when the channel's
        # 'Restrict saving content' switch was turned on AFTER this post was
        # published — Telegram still refuses the copy. Remember the target so
        # every later post skips the doomed attempt, then download + re-upload.
        _PROTECTED.add(target)
        if getattr(msg, "media", None) is None:
            raise
        log.warning("target %s is content-protected (ChatForwardsRestricted) — "
                    "falling back to download + re-upload for cover msg %s",
                    target, getattr(msg, "id", "?"))
        return await download_and_reupload(client, dbc, msg,
                                           caption=msg.message or "",
                                           buttons=msg.buttons, source=target)


async def send_media(client, src, msgs, dbc):
    """EVERYTHING the media bot delivered — videos (any format), srt,
    images, stickers, text notes — re-sent in original order, each
    verified. Every file keeps its original attributes (duration,
    thumbnail, filename, streaming) because Telegram copies the stored
    file server-side; we never touch the bytes."""
    ok = 0
    for m in msgs:
        try:
            await _send_one(client, dbc, m, src)
        except ChatForwardsRestrictedError:
            # v60: a protected MEDIA source can never kill a post either
            await download_and_reupload(client, dbc, m, source=src)
        ok += 1
    log.info("delivered %d/%d message(s) to DB channel", ok, len(msgs))


async def deliver_post_bundle(client, target, cover_msg, src, msgs, dbc):
    """v39: ONE post's complete delivery, serialized per DB channel.
    Holds the per-DB lock for the WHOLE bundle: cover post first, then all
    its videos/srt/other — another userbot's post waits in line, so parallel
    scraping can never interleave two posts in the DB channel. DB2 inherits
    the same ordering via botapi's own per-DB mirror lock."""
    async with delivery_lock(dbc):
        log.info("delivering bundle for post %s -> DB %s (lock held)",
                 getattr(cover_msg, "id", "?"), dbc)
        await send_cover(client, target, cover_msg, dbc)
        await send_media(client, src, msgs, dbc)
