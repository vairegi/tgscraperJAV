"""forwarder.py — send cover post + media to the DB channel.
v5: disk-based copy-mode. v4 buffered whole videos in RAM (BytesIO) which
(a) OOM-killed the 512MB Render instance and (b) lost video attributes
(duration/dimensions/streaming) so videos landed as generic documents.
Now media streams to a TEMP FILE on disk (no RAM usage), and is re-uploaded
WITH the original document attributes — duration, resolution, streaming,
filename — so it arrives looking exactly like what the source sent.
Spoiler + buttons + captions preserved, no forward tag, per-file attributes
mean every file keeps its real format. Temp files are deleted after send."""
import os
import tempfile
from telethon.tl import types

_KEEP_ATTRS = (types.DocumentAttributeVideo, types.DocumentAttributeAudio,
               types.DocumentAttributeFilename, types.DocumentAttributeSticker,
               types.DocumentAttributeImageSize)


def _spoiler(msg):
    return bool(getattr(getattr(msg, "media", None), "spoiler", False))


def _attrs(msg):
    doc = getattr(msg, "document", None)
    if not doc:
        return None
    keep = [a for a in (doc.attributes or []) if isinstance(a, _KEEP_ATTRS)]
    return keep or None


async def _download(client, msg, tmpdir):
    name = getattr(getattr(msg, "file", None), "name", None)
    ext = os.path.splitext(name)[1] if name else ""
    path = os.path.join(tmpdir, f"{msg.id}{ext}")
    await client.download_media(msg, file=path)  # streams to disk, not RAM
    return path


async def _thumb(client, msg, tmpdir):
    doc = getattr(msg, "document", None)
    if not doc or not getattr(doc, "thumbs", None):
        return None
    tpath = os.path.join(tmpdir, f"{msg.id}_thumb.jpg")
    try:
        await client.download_media(msg, file=tpath, thumb=-1)
        return tpath
    except Exception:
        return None


async def _send_one(client, dbc, msg, tmpdir, caption=None, buttons=None):
    if caption is None:
        caption = msg.message or ""
    try:
        path = await _download(client, msg, tmpdir)
    except Exception:
        # download failed (rare) — last-resort direct resend of the reference
        await client.send_file(dbc, msg.media, caption=caption,
                               buttons=buttons, spoiler=_spoiler(msg))
        return
    thumb = await _thumb(client, msg, tmpdir)
    try:
        try:
            await client.send_file(dbc, path, caption=caption, buttons=buttons,
                                   spoiler=_spoiler(msg), attributes=_attrs(msg),
                                   thumb=thumb, supports_streaming=True,
                                   force_document=False)
        except Exception:
            # attributes/thumb rejected (e.g. sticker set mismatch) — plain send
            await client.send_file(dbc, path, caption=caption, buttons=buttons,
                                   spoiler=_spoiler(msg), force_document=False)
    finally:
        for p in (path, thumb):
            if p and os.path.exists(p):
                os.remove(p)


async def send_cover(client, target, msg, dbc):
    """Cover post from the target channel: same image (spoiler kept), same
    caption, same buttons — as a fresh message, no forward tag."""
    tmpdir = tempfile.mkdtemp(prefix="tgscrap_")
    try:
        await _send_one(client, dbc, msg, tmpdir,
                        caption=msg.message or "", buttons=msg.buttons)
    finally:
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


async def send_media(client, src, msgs, dbc):
    """Whatever the media bot delivered — videos (any format), srt, images,
    stickers — re-sent in original order with original per-file attributes,
    so the DB channel gets them in EXACTLY the format the bot sent."""
    tmpdir = tempfile.mkdtemp(prefix="tgscrap_")
    try:
        for m in msgs:
            await _send_one(client, dbc, m, tmpdir)
    finally:
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
