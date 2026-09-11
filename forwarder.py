"""forwarder.py — send cover post + media to the DB channel.
Uses forward (cheap, no re-upload); falls back to copy if the source
channel has forwarding restricted."""

async def send_cover(client, target, msg, dbc):
    try:
        await client.forward_messages(dbc, msg.id, target)
    except Exception:
        await client.send_file(dbc, msg.media, caption=msg.message or "",
                               buttons=msg.buttons)

async def send_media(client, src, msgs, dbc):
    try:
        await client.forward_messages(dbc, [m.id for m in msgs], src)
    except Exception:
        for m in msgs:
            await client.send_file(dbc, m.media, caption=m.message or "")
