"""forwarder.py — send cover post + media to the DB channel.
v17: copy-mode BY REFERENCE — no download, no temp files, no disk I/O.
Every media message is re-sent with send_file(dbc, msg.media): Telethon
reuses the message's existing Telegram file reference (InputPhoto /
InputDocument), so Telegram copies the file server-to-server straight into
the DB channel. The result looks exactly like a fresh post — caption,
spoiler flag, buttons, playable video with thumbnail/duration/filename all
preserved — but WITHOUT the "Forwarded from" tag (this is a fresh
send_file, never forward_messages). The bytes never leave Telegram's
servers, so RAM/disk stay flat on the 512MB Render free tier no matter
how big the video is (700MB+ included).

Removes everything v16 needed disk for: tempfile, download_media,
_download, _thumb and all filesystem cleanup."""
import asyncio
import logging

from telethon.errors import FileReferenceExpiredError

log = logging.getLogger("forwarder")

SEND_RETRIES = 3
RETRY_DELAY = 5  # seconds between send attempts


def _spoiler(msg):
    return bool(getattr(getattr(msg, "media", None), "spoiler", False))


async def _send_one(client, dbc, msg, source, caption=None, buttons=None):
    """Re-send ONE message's media into the DB channel by reference.

    msg.media carries the Telegram-side file reference, so the file is
    copied server-to-server: nothing is downloaded to the Render disk and
    nothing is re-uploaded. No forward tag, because this is a fresh
    send_file — not forward_messages.

    If Telegram reports the file reference expired (can happen on OLD
    cover posts), the message is refetched from its source chat to obtain
    a fresh reference, then retried."""
    if caption is None:
        caption = msg.message or ""
    media = getattr(msg, "media", None)
    if media is None:
        # nothing attachable (e.g. text-only) — don't lose the post silently
        if caption:
            await client.send_message(dbc, caption, buttons=buttons)
        return
    last_err = None
    for attempt in range(1, SEND_RETRIES + 1):
        try:
            await client.send_file(dbc, media, caption=caption,
                                   buttons=buttons, spoiler=_spoiler(msg))
            return
        except FileReferenceExpiredError as e:
            # stale reference (old cover post): refetch the message from its
            # source chat -> fresh file_reference -> retry with fresh media
            log.info("file reference expired for msg %s — refetching",
                     getattr(msg, "id", "?"))
            last_err = e
            try:
                fresh = await client.get_messages(source, ids=msg.id)
            except Exception as ge:
                last_err = ge
                break
            if not fresh or not getattr(fresh, "media", None):
                break  # refetch found nothing — give up with the original error
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
    """Whatever the media bot delivered — videos (any format), srt, images,
    stickers — re-sent in original order by reference. Every file keeps its
    original attributes (duration, thumbnail, filename, streaming) because
    Telegram copies the stored file server-side; we never touch the bytes."""
    for m in msgs:
        await _send_one(client, dbc, m, src)
