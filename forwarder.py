"""forwarder.py — send cover post + media to the DB channel.
v4: copy-mode with BYTES. File references (msg.media) are cryptographically
bound to the account that received them — with multi-account rotation, acc2
re-sending media fetched by acc1 gets MediaEmptyError on every post (your
log: posts 99-114, all at send_cover). Downloading to bytes first removes
the binding: any account can re-upload them. No forward tag, spoiler kept,
albums grouped."""
import io


def _spoiler(msg):
    return bool(getattr(getattr(msg, "media", None), "spoiler", False))


async def _bytes_of(client, msg):
    """Download media to in-memory bytes with the account that fetched the
    message (its session has the access hashes), so any account can re-send."""
    buf = io.BytesIO()
    await client.download_media(msg, file=buf)
    buf.seek(0)
    name = getattr(getattr(msg, "file", None), "name", None)
    if name:
        buf.name = name
    return buf


async def send_cover(client, target, msg, dbc):
    """Cover post (spoiler image + caption + buttons) as a fresh message."""
    try:
        buf = await _bytes_of(client, msg)
        await client.send_file(dbc, buf, caption=msg.message or "",
                               buttons=msg.buttons, spoiler=_spoiler(msg))
    except Exception:
        # download path failed (rare) — fall back to direct re-send
        await client.send_file(dbc, msg.media, caption=msg.message or "",
                               buttons=msg.buttons, spoiler=_spoiler(msg))


async def send_media(client, src, msgs, dbc):
    """Re-send everything the media bot delivered (videos of any format,
    srt, images, stickers) — no forward tag, albums grouped."""
    groups = {}
    for m in msgs:
        groups.setdefault(getattr(m, "grouped_id", None) or m.id, []).append(m)
    for group in groups.values():
        caption = group[0].message or ""
        if len(group) == 1:
            m = group[0]
            try:
                buf = await _bytes_of(client, m)
                await client.send_file(dbc, buf, caption=caption, spoiler=_spoiler(m))
            except Exception:
                await client.send_file(dbc, m.media, caption=caption, spoiler=_spoiler(m))
        else:
            bufs = []
            try:
                for m in group:
                    bufs.append(await _bytes_of(client, m))
                await client.send_file(dbc, bufs, caption=caption)
            except Exception:
                for m in group:  # album download failed -> send one by one
                    try:
                        buf = await _bytes_of(client, m)
                        await client.send_file(dbc, buf, caption=m.message or "",
                                               spoiler=_spoiler(m))
                    except Exception:
                        pass
