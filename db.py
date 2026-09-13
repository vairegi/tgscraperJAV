"""db.py — MongoDB (Motor) layer: config, per-target progress, stats, failures.
Targets are stored as a list of pairs: [{"id": <channel>, "db_id": <db channel>}]
— each target channel has its OWN database channel."""
import time
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI, DB_NAME

_client = None
_db = None

def db():
    global _client, _db
    if _db is None:
        _client = AsyncIOMotorClient(MONGO_URI)
        _db = _client[DB_NAME]
    return _db

async def set_config(key, value):
    await db().config.update_one({"_id": "config"}, {"$set": {key: value}}, upsert=True)

async def get_config(key=None):
    doc = await db().config.find_one({"_id": "config"}) or {}
    return doc.get(key) if key else doc

# ---------------- targets (multi-channel, per-target DB) ----------------

async def get_targets():
    """List of {"id": ..., "db_id": ...|None}. Auto-migrates legacy shapes:
    plain id list, or single target_id + global db_id."""
    doc = await db().config.find_one({"_id": "config"}) or {}
    out = []
    for t in doc.get("targets") or []:
        if isinstance(t, dict):
            out.append({"id": t.get("id") or t.get("target_id"), "db_id": t.get("db_id"),
                        "paused": bool(t.get("paused", False))})
        else:
            out.append({"id": t, "db_id": None, "paused": False})
    out = [t for t in out if t["id"] is not None]
    if not out and doc.get("target_id"):
        out = [{"id": doc["target_id"], "db_id": doc.get("db_id"), "paused": False}]
    return out

async def _save_targets(targets):
    upd = {"targets": targets}
    await db().config.update_one({"_id": "config"},
        {"$set": {"targets": targets,
                  "target_id": targets[0]["id"] if targets else None}}, upsert=True)
    return targets

async def add_target(tid, db_id=None):
    targets = await get_targets()
    for t in targets:
        if t["id"] == tid:
            if db_id is not None:
                t["db_id"] = db_id
            return await _save_targets(targets)
    targets.append({"id": tid, "db_id": db_id})
    return await _save_targets(targets)

async def remove_target(tid):
    """Remove from the active list. Progress is KEPT — re-adding later
    resumes where it left off."""
    targets = [t for t in await get_targets() if t["id"] != tid]
    return await _save_targets(targets)

async def set_target_db(tid, db_id):
    targets = await get_targets()
    for t in targets:
        if t["id"] == tid:
            t["db_id"] = db_id
            return await _save_targets(targets)
    return None  # target not found

async def set_target_paused(tid, paused):
    """Per-target pause flag — persisted in Mongo so it survives Render
    restarts, exactly like progress. Returns updated list, None if unknown."""
    targets = await get_targets()
    for t in targets:
        if t["id"] == tid:
            t["paused"] = bool(paused)
            return await _save_targets(targets)
    return None  # target not found

# ---------------- custom LINK_BOT button labels ----------------
# Owner-managed via /linkbutton in the control bot. The userbot matches the
# LINK_BOT link button against the built-in default (config.BTN_SHORT_LINK)
# PLUS every label in this list — so a bot button rename never needs a
# redeploy, just /linkbutton <new text>.

async def get_link_buttons():
    doc = await db().config.find_one({"_id": "config"}) or {}
    return list(doc.get("link_buttons") or [])

async def add_link_button(label):
    """Append a label; returns (list, added?). De-dupes with the same
    Unicode-fold the matcher uses, so 'Get Link' and '𝗚𝗲𝘁 𝗟𝗶𝗻𝗸' are one entry."""
    from scraper import norm
    buttons = await get_link_buttons()
    if any(norm(b) == norm(label) for b in buttons):
        return buttons, False
    buttons.append(label)
    await set_config("link_buttons", buttons)
    return buttons, True

async def remove_link_button(n):
    """Remove label #n (1-based, the numbering shown by /linkbutton).
    Returns (list, removed_label), or None if n is out of range."""
    buttons = await get_link_buttons()
    if not (1 <= n <= len(buttons)):
        return None
    removed = buttons.pop(n - 1)
    await set_config("link_buttons", buttons)
    return buttons, removed

# ---------------- progress (per target id) ----------------

async def get_progress(target_id):
    doc = await db().progress.find_one({"_id": str(target_id)})
    return (doc or {}).get("last_id", 0)

async def set_progress(target_id, last_id):
    await db().progress.update_one({"_id": str(target_id)},
        {"$set": {"last_id": last_id, "ts": time.time()}}, upsert=True)

async def reset_progress(target_id):
    await db().progress.delete_one({"_id": str(target_id)})

async def set_last_post(target_id, post_id):
    await db().progress.update_one({"_id": str(target_id)},
        {"$set": {"last_post": post_id}}, upsert=True)

async def get_last_post(target_id):
    doc = await db().progress.find_one({"_id": str(target_id)})
    return (doc or {}).get("last_post")

# ---------------- stats + failures ----------------

async def incr(field, n=1):
    await db().stats.update_one({"_id": "stats"}, {"$inc": {field: n}}, upsert=True)

async def get_stats():
    return await db().stats.find_one({"_id": "stats"}) or {}

async def add_failure(post_id, stage, reason):
    d = db()
    await d.failures.insert_one({"post_id": post_id, "stage": stage,
                                 "reason": str(reason)[:500], "ts": time.time()})
    await incr("failures")
    cnt = await d.failures.count_documents({})
    if cnt > 50:
        ids = [x["_id"] async for x in d.failures.find().sort("ts", 1).limit(cnt - 50)]
        await d.failures.delete_many({"_id": {"$in": ids}})

async def get_failures(limit=10):
    return [x async for x in db().failures.find().sort("ts", -1).limit(limit)]
