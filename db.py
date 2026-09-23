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
                        "paused": bool(t.get("paused", False)),
                        "db2_id": t.get("db2_id"), "avoid": list(t.get("avoid") or [])})
        else:
            out.append({"id": t, "db_id": None, "paused": False, "db2_id": None, "avoid": []})
    out = [t for t in out if t["id"] is not None]
    if not out and doc.get("target_id"):
        out = [{"id": doc["target_id"], "db_id": doc.get("db_id"), "paused": False,
                "db2_id": None, "avoid": []}]
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
    targets.append({"id": tid, "db_id": db_id, "db2_id": None, "avoid": []})
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

async def set_target_db2(tid, db2_id):
    """Set/clear a target's DB2 clean-mirror channel (None disables)."""
    targets = await get_targets()
    for t in targets:
        if t["id"] == tid:
            t["db2_id"] = db2_id
            return await _save_targets(targets)
    return None  # target not found

async def get_avoids(tid):
    for t in await get_targets():
        if t["id"] == tid:
            return list(t.get("avoid") or [])
    return []

async def add_avoid(tid, text):
    """Append an avoid-string for a target's DB2 mirror. Returns (list, added?)."""
    targets = await get_targets()
    for t in targets:
        if t["id"] == tid:
            av = list(t.get("avoid") or [])
            if text in av:
                return av, False
            av.append(text)
            t["avoid"] = av
            await _save_targets(targets)
            return av, True
    return None, False

async def remove_avoid(tid, n):
    """Remove avoid-string #n (1-based) for a target. Returns (list, removed) or None."""
    targets = await get_targets()
    for t in targets:
        if t["id"] == tid:
            av = list(t.get("avoid") or [])
            if not (1 <= n <= len(av)):
                return None
            removed = av.pop(n - 1)
            t["avoid"] = av
            await _save_targets(targets)
            return av, removed
    return None

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

# ---------------- extra control-bot admins (owner-managed) ----------------
# The owner (ADMIN_USER_ID env) is always admin. Ids added here get FULL
# control-bot access, exactly like the owner. Mongo-backed -> survives
# Render restarts/redeploys.

async def get_admins():
    doc = await db().config.find_one({"_id": "config"}) or {}
    return list(doc.get("admins") or [])

async def add_admin(uid):
    admins = await get_admins()
    if uid not in admins:
        admins.append(uid)
        await set_config("admins", admins)
    return admins

async def remove_admin(uid):
    """Returns (admins, removed?)."""
    admins = await get_admins()
    if uid in admins:
        admins.remove(uid)
        await set_config("admins", admins)
        return admins, True
    return admins, False

# ---------------- v39: multi-bypass pool + domain routing ----------------
# The pool is an ORDERED list of bypass endpoints (bot @usernames or group
# ids). /bypass manages slot #1, /addbypass appends more, /removebypass
# deletes one. bypass_domains maps a short-link domain (or 'name.*' wildcard)
# to a specific endpoint — matched links go ONLY to that endpoint, everything
# else tries every pool bot in order. The legacy bypass_id/alt_bypass_id
# config keys stay in Mongo; the pool is built from them on first read.

async def _ensure_bypass_migrated():
    doc = await db().config.find_one({"_id": "config"}) or {}
    pool = doc.get("bypass_pool")
    if isinstance(pool, list):
        return list(pool)
    pool = []
    if doc.get("bypass_id") is not None:
        pool.append(doc["bypass_id"])
    await db().config.update_one({"_id": "config"}, {"$set": {"bypass_pool": pool}}, upsert=True)
    return pool

async def get_bypass_pool():
    """Ordered bypass endpoints. Auto-migrates legacy bypass_id on first read."""
    return await _ensure_bypass_migrated()

async def add_bypass(v):
    """Append an endpoint to the pool (deduped). Keeps bypass_id = pool[0]
    so old code paths and /status keep working. Returns (pool, added?)."""
    pool = await _ensure_bypass_migrated()
    if v in pool:
        return pool, False
    pool.append(v)
    await set_config("bypass_pool", pool)
    await set_config("bypass_id", pool[0])
    return pool, True

async def remove_bypass(n):
    """Remove pool endpoint #n (1-based, numbering shown by /bypasslist).
    Returns (pool, removed) or None if n is out of range."""
    pool = await _ensure_bypass_migrated()
    if not (1 <= n <= len(pool)):
        return None
    removed = pool.pop(n - 1)
    await set_config("bypass_pool", pool)
    await set_config("bypass_id", pool[0] if pool else None)
    return pool, removed

async def get_bypass_domains():
    doc = await db().config.find_one({"_id": "config"}) or {}
    return list(doc.get("bypass_domains") or [])

async def add_bypass_domain(domain, endpoint):
    """Map a short-link domain (or 'name.*' wildcard) to a specific bypass
    endpoint. One rule per domain — re-adding the same domain re-points it.
    Returns the full rules list."""
    from scraper import norm_domain
    domain = norm_domain(domain)
    rules = await get_bypass_domains()
    rules = [r for r in rules if r.get("domain") != domain]
    rules.append({"domain": domain, "endpoint": endpoint})
    await set_config("bypass_domains", rules)
    return rules

async def remove_bypass_domain(n):
    """Remove domain rule #n (1-based, numbering shown by /bypasslist).
    Returns (rules, removed_rule) or None if n is out of range."""
    rules = await get_bypass_domains()
    if not (1 <= n <= len(rules)):
        return None
    removed = rules.pop(n - 1)
    await set_config("bypass_domains", rules)
    return rules, removed

# ---------------- v40: GLOBAL DB2 text cleaning ----------------
# /avoid adds strings stripped from EVERY target's DB2 mirror captions;
# /replaceword adds (old -> new) rewrites applied BEFORE the avoid-strip.
# Both apply only to DB2 captions — DB posts are never touched. Per-target
# /avoidtext keeps working on top of these globals.

async def get_global_avoids():
    doc = await db().config.find_one({"_id": "config"}) or {}
    return list(doc.get("global_avoids") or [])

async def add_global_avoid(text):
    av = await get_global_avoids()
    if text in av:
        return av, False
    av.append(text)
    await set_config("global_avoids", av)
    return av, True

async def remove_global_avoid(n):
    av = await get_global_avoids()
    if not (1 <= n <= len(av)):
        return None
    removed = av.pop(n - 1)
    await set_config("global_avoids", av)
    return av, removed

async def get_replace_words():
    doc = await db().config.find_one({"_id": "config"}) or {}
    return [dict(p) for p in (doc.get("replace_words") or [])]

async def add_replace_word(old, new):
    pairs = await get_replace_words()
    pairs = [p for p in pairs if p.get("old") != old]
    pairs.append({"old": old, "new": new})
    await set_config("replace_words", pairs)
    return pairs

async def remove_replace_word(n):
    pairs = await get_replace_words()
    if not (1 <= n <= len(pairs)):
        return None
    removed = pairs.pop(n - 1)
    await set_config("replace_words", pairs)
    return pairs, removed

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
