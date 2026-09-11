"""db.py — MongoDB (Motor) layer: config, progress, stats, failures."""
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

async def get_progress(target_id):
    doc = await db().progress.find_one({"_id": str(target_id)})
    return (doc or {}).get("last_id", 0)

async def set_progress(target_id, last_id):
    await db().progress.update_one({"_id": str(target_id)},
        {"$set": {"last_id": last_id, "ts": time.time()}}, upsert=True)

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
    if cnt > 50:  # keep last 50
        ids = [x["_id"] async for x in d.failures.find().sort("ts", 1).limit(cnt - 50)]
        await d.failures.delete_many({"_id": {"$in": ids}})

async def get_failures(limit=10):
    return [x async for x in db().failures.find().sort("ts", -1).limit(limit)]

async def set_last_post(target_id, post_id):
    await db().progress.update_one({"_id": str(target_id)},
        {"$set": {"last_post": post_id}}, upsert=True)

async def get_last_post(target_id):
    doc = await db().progress.find_one({"_id": str(target_id)})
    return (doc or {}).get("last_post")

async def reset_progress(target_id):
    """Clear progress + last_post for a target (next scan starts from msg 1)."""
    await db().progress.delete_one({"_id": str(target_id)})
