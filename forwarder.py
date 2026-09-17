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
preserved because the same server-side file reference is reused."""
import asyncio
import logging

from telethon.errors import FileReferenceExpiredError, MediaInvalidError

log = logging.getLogger("forwarder")

SEND_RETRIES = 3
RETRY_DELAY = 5  # seconds between send attempts


class StaleRef(Exception):
    """Raised when Telegram accepted a send but the new message has no
    media — the file reference was dead server-side. Treated exactly like
    FileReferenceExpiredError: refetch the source message and retry."""
    pass


def _spoiler(msg):
    return bool(getattr(getattr(msg, "media", None), "spoiler", False))


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
        except (FileReferenceExpiredError, StaleRef, MediaInvalidError) as e:
            # stale/dead reference: refetch from the source chat -> fresh
            # file_reference -> retry with the fresh media
            log.info("msg %s reference unusable (%s) — refetching",
                     getattr(msg, "id", "?"), type(e).__name__)
            last_err = e
            fresh = await _refetch(client, msg, source)
            if fresh is None:
                break  # refetch found nothing — give up with the error
            msg = fresh
            media = fresh.media
        except Exception as e:
            last_err = e
            log.warning("send attempt %d/%d failed for msg %s: %s",
                        attempt, SEND_RETRIES, getattr(msg, "id", "?"), e)
        if attempt < SEND_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    raise last_err


async def send_cover(client, target, msg, dbc):
    """Cover post from the target channel: same image (spoiler kept), same
    caption, same buttons — as a fresh message, no forward tag, no download."""
    await _send_one(client, dbc, msg, target,
                    caption=msg.message or "", buttons=msg.buttons)


async def send_media(client, src, msgs, dbc):
    """EVERYTHING the media bot delivered — videos (any format), srt,
    images, stickers, text notes — re-sent in original order, each
    verified. Every file keeps its original attributes (duration,
    thumbnail, filename, streaming) because Telegram copies the stored
    file server-side; we never touch the bytes."""
    ok = 0
    for m in msgs:
        await _send_one(client, dbc, m, src)
        ok += 1
    log.info("delivered %d/%d message(s) to DB channel", ok, len(msgs))
