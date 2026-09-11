"""forwarder.py — send cover post + media to the DB channel.
v3: COPY mode (no 'Forwarded from' tag) using send_file — Telethon's
send_message() has no 'spoiler' kwarg; send_file() does. Spoiler preserved,
albums grouped, zero forward tags."""


def _spoiler(msg):
    return bool(getattr(getattr(msg, "media", None), "spoiler", False))


async def send_cover(client, target, msg, dbc):
    """Re-send the cover post (media + caption + original buttons) to the DB
    channel as a fresh message — no forward tag, spoiler preserved."""
    await client.send_file(dbc, msg.media, caption=msg.message or "",
                           buttons=msg.buttons, spoiler=_spoiler(msg))


async def send_media(client, src, msgs, dbc):
    """Re-send videos + srt files to the DB channel, no forward tag.
    Albums are grouped per grouped_id so multi-video posts stay together."""
    groups = {}
    for m in msgs:
        groups.setdefault(getattr(m, "grouped_id", None) or m.id, []).append(m)
    for group in groups.values():
        files = [m.media for m in group if m.media]
        if not files:
            continue
        caption = group[0].message or ""
        if len(files) == 1:
            await client.send_file(dbc, files[0], caption=caption,
                                   spoiler=_spoiler(group[0]))
        else:
            await client.send_file(dbc, files, caption=caption)
