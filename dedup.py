"""dedup.py — v45/v45.1: DB2-cover fingerprint index + duplicate-skip gate.

Fingerprints ONLY DB2 cover posts (m.photo). Match = exact OR rapidfuzz
token_set_ratio >= 90. One scan per DB2 ever; siblings sharing a DB2 share
the index; every scraped cover auto-grows all siblings. /pause returns
pending skip details inline; batches DM after 10 per target."""
import asyncio, logging, re, time, unicodedata
import db as DB

log = logging.getLogger("dedup")

try:
    from rapidfuzz.fuzz import token_set_ratio as _ratio
except Exception:                       # pragma: no cover
    import difflib
    def _ratio(a, b): return difflib.SequenceMatcher(None, a, b).ratio() * 100

DUP_THRESHOLD = 90.0
DUP_BATCH = 10
FP_CAP = 20000
FP_MAX_KEEP = 60000

_FPS, _INDEXED, _BUILDING, _LOCKS = {}, set(), {}, {}
_PENDING_SKIPS, _COUNTS, _DB2_OWNERS = {}, {}, {}


def _lock(t):
    if t not in _LOCKS: _LOCKS[t] = asyncio.Lock()
    return _LOCKS[t]


def _norm(s): return unicodedata.normalize("NFKD", s or "").lower()


_NONSTORY_RE = re.compile(
    r"(?:^|\b)(?:episode|subtitles?|censor(?:ship|ed)?|rating|network|audio|"
    r"quality|resolution|size|duration|genre|genres|studio|release|language|"
    r"source|seed|leech)\s*[:\-]", re.I)


def fingerprint_cover(caption):
    if not caption: return ""
    head = caption
    if "\u27aa" in head:
        head = head.split("\u27aa", 1)[0]
    else:
        kept = []
        for ln in head.splitlines():
            if _NONSTORY_RE.search(ln): break
            kept.append(ln)
        head = "\n".join(kept)
    s = _norm(head)
    s = re.sub("[\u200b-\u200f\ufeff]", "", s)
    s = re.sub(r"(?:https?://)?\S*(?:t\.me|telegram\.me)/\S+|https?://\S+", " ", s)
    s = re.sub(r"@[A-Za-z0-9_]+", " ", s)
    toks = re.findall(r"\w+", s)
    return " ".join(t for t in toks if len(t) >= 3 and not t.isdigit())


async def get_cover_fp(t):
    return await DB.db().cover_fp.find_one({"_id": str(t)})


async def init_cover_fp(t, db2, fps, scanned, secs):
    await DB.db().cover_fp.update_one({"_id": str(t)}, {"$set": {
        "target_id": t, "db2_id": db2, "scanned_at": time.time(),
        "scanned_count": scanned, "index_build_time": secs,
        "fingerprints": [{"fp": fp, "first_seen_msg": mid, "hits": [mid],
                          "added_at": time.time()} for fp, mid in fps]}}, upsert=True)


async def add_cover_fp(t, fp, mid):
    if not fp: return False
    res = await DB.db().cover_fp.update_one(
        {"_id": str(t), "fingerprints.fp": {"$ne": fp}},
        {"$push": {"fingerprints": {
            "$each": [{"fp": fp, "first_seen_msg": mid, "hits": [mid],
                       "added_at": time.time()}],
            "$position": 0, "$slice": -FP_CAP}},
         "$setOnInsert": {"target_id": t}}, upsert=True)
    return bool(res.modified_count or res.upserted_id)


async def get_db2_index(db2):
    return await DB.db().db2_cover_index.find_one({"_id": str(db2)})


async def set_db2_index(db2, last_mid):
    await DB.db().db2_cover_index.update_one({"_id": str(db2)},
        {"$set": {"last_indexed_msg_id": last_mid, "last_scan_ts": time.time()}},
        upsert=True)


# ---- v45.1 sibling sync (targets sharing one DB2) ----
async def db2_owners(db2):
    """Every target id whose DB2 = db2 (60s cache)."""
    e = _DB2_OWNERS.get(db2)
    if e and time.time() - e[1] < 60: return list(e[0])
    owners = [t["id"] for t in await DB.get_targets() if t.get("db2_id") == db2]
    _DB2_OWNERS[db2] = (owners, time.time())
    return owners


async def share_db2(target_id, db2_id):
    """Copy a sibling's existing index into this target (RAM + Mongo).
    Returns the sibling id when shared, else None."""
    for sib in await db2_owners(db2_id):
        if sib == target_id: continue
        fps = None
        if sib in _FPS and _FPS[sib]:
            fps = list(_FPS[sib])
        elif sib in _INDEXED:
            doc = await get_cover_fp(sib)
            fps = [e["fp"] for e in (doc or {}).get("fingerprints") or []]
        if fps:
            _FPS[target_id] = fps[:FP_MAX_KEEP]
            _INDEXED.add(target_id)
            await DB.db().cover_fp.update_one({"_id": str(target_id)},
                {"$set": {"target_id": target_id, "db2_id": db2_id,
                          "scanned_at": time.time(), "shared_from": sib,
                          "fingerprints": [{"fp": fp, "first_seen_msg": 0,
                                            "hits": [], "added_at": time.time()}
                                           for fp in fps]}}, upsert=True)
            return sib
    return None


# ---- matching ----
async def _load_from_mongo(t):
    doc = await get_cover_fp(t)
    fps = [e["fp"] for e in (doc or {}).get("fingerprints") or []]
    _FPS[t] = fps[:FP_MAX_KEEP]
    return bool(fps)


async def ensure_index(t):
    if t in _FPS: return bool(_FPS[t])
    async with _lock(t):
        if t in _FPS: return bool(_FPS[t])
        return await _load_from_mongo(t)


async def find_dup(t, sig):
    if not sig or not await ensure_index(t): return None
    fps = _FPS.get(t) or []
    if sig in fps: return sig, 100.0
    best, best_fp = 0.0, None
    for fp in fps:
        r = _ratio(sig, fp)
        if r > best: best, best_fp = r, fp
    return (best_fp, round(best, 1)) if best >= DUP_THRESHOLD else None


async def remember(t, fp, mid, db2_id=None):
    """Grow this target AND every sibling on the same DB2 (v45.1)."""
    if not fp: return
    owners = [t]
    if db2_id is None:
        doc = await get_cover_fp(t)
        db2_id = (doc or {}).get("db2_id")
    if db2_id:
        owners = await db2_owners(db2_id) or [t]
        if t not in owners: owners.append(t)
    for tid in owners:
        async with _lock(tid):
            lst = _FPS.setdefault(tid, [])
            if fp not in lst:
                lst.insert(0, fp); del lst[FP_MAX_KEEP:]
        await add_cover_fp(tid, fp, mid)


async def already_indexed(target_id, caption):
    try: return await find_dup(target_id, fingerprint_cover(caption))
    except Exception as e:
        log.warning("dedup: gate error on target %s (%s) — scraping anyway", target_id, e)
        return None


async def note_scraped(target_id, caption, mid, db2_id=None):
    try: await remember(target_id, fingerprint_cover(caption), mid, db2_id=db2_id)
    except Exception as e: log.warning("dedup: remember failed (%s)", e)


# ---- batched skip notifications ----
def _count(t): return _COUNTS.get(t, 0)


def status_lines():
    if not _COUNTS: return ""
    out = ["🧬 Duplicates skipped (this session):"]
    for tid, n in sorted(_COUNTS.items()): out.append(f"  • {tid}: {n}")
    return "\n" + "\n".join(out)


def format_batch(target_id, entries, reason):
    lines = [f"📋 DUPLICATES SKIPPED — target {target_id} ({reason})"]
    for s in entries: lines.append(f"• post {s['msg_id']} — {s['score']}% match in DB2")
    fp = entries[-1].get("match_fp") if entries else ""
    if fp: lines.append(f"last match: {fp}…")
    lines.append(f"total dupes this session: {_count(target_id)}")
    return "\n".join(lines)


async def _dm_admins(text):
    from flow import state
    from config import ADMIN_USER_ID
    import botapi
    ctl = getattr(botapi, "bot", None)
    async def _one(uid):
        if ctl is not None:
            try: await ctl.send_message(uid, text, parse_mode="md"); return True
            except Exception: pass
        try:
            c = getattr(state, "scrape_client", None)
            if c is not None:
                await c.send_message(uid, text, parse_mode="md"); return True
        except Exception: pass
        return False
    sent = False
    if ADMIN_USER_ID: sent = await _one(ADMIN_USER_ID) or sent
    for a in await DB.get_admins():
        if a != ADMIN_USER_ID: sent = await _one(a) or sent
    if not sent: log.warning("dedup: skip batch could not be delivered: %s", text[:120])


async def flush_skips(target_id, reason="batch of %d" % DUP_BATCH):
    q = _PENDING_SKIPS.get(target_id) or []
    if not q: return
    _PENDING_SKIPS[target_id] = []
    try: await _dm_admins(format_batch(target_id, q, reason))
    except Exception as e: log.warning("dedup: flush_skips DM failed (%s)", e)


def consume_skips(target_id):
    """Take pending skips WITHOUT sending — used by /pause and /resume replies."""
    return _PENDING_SKIPS.pop(target_id, [])


async def _record_skip(target_id, msg_id, score, match_fp):
    entry = {"msg_id": msg_id, "score": score,
             "match_fp": (match_fp or "")[:120], "ts": time.time()}
    _PENDING_SKIPS.setdefault(target_id, []).append(entry)
    _COUNTS[target_id] = _COUNTS.get(target_id, 0) + 1
    if len(_PENDING_SKIPS[target_id]) >= DUP_BATCH:
        await flush_skips(target_id)


async def record_skip(target_id, msg_id, score, match_fp):
    try:
        await _record_skip(target_id, msg_id, score, match_fp)
        await DB.incr("dupes_skipped")
    except Exception as e: log.warning("dedup: record_skip failed (%s)", e)


def is_indexed(t): return t in _INDEXED
def invalidate(t): _INDEXED.discard(t); _FPS.pop(t, None)


async def scan_db2(client, target_id, db2_id, progress_cb=None):
    """ONE-TIME scan; also seeds every sibling target sharing this DB2."""
    if target_id in _INDEXED and _FPS.get(target_id):
        return {"status": "cached", "count": len(_FPS[target_id])}
    tsk = _BUILDING.get(target_id)
    if tsk and not tsk.done(): return {"status": "already_running"}
    siblings = [s for s in await db2_owners(db2_id) if s != target_id]

    async def _run():
        t0 = time.time()
        fps, count, covers = [], 0, 0
        async for m in client.iter_messages(db2_id):
            count += 1
            if count % 100 == 0:
                await asyncio.sleep(0.1)
                if progress_cb:
                    try: await progress_cb(count, covers)
                    except Exception: pass
            if not m.photo: continue
            sig = fingerprint_cover(m.message or "")
            if not sig: continue
            covers += 1
            fps.append((sig, m.id))
        await init_cover_fp(target_id, db2_id, fps, count, round(time.time() - t0, 1))
        await set_db2_index(db2_id, fps[0][1] if fps else 0)
        async with _lock(target_id):
            _FPS[target_id] = [fp for fp, _ in fps][:FP_MAX_KEEP]
        _INDEXED.add(target_id)
        for sib in siblings:
            async with _lock(sib):
                _FPS[sib] = [fp for fp, _ in fps][:FP_MAX_KEEP]
            _INDEXED.add(sib)
            await init_cover_fp(sib, db2_id, fps, count, round(time.time() - t0, 1))
        log.info("dedup: target %s indexed — %d fp from DB2 %s (+%d sibling(s))",
                 target_id, len(fps), db2_id, len(siblings))
        return {"status": "done", "count": len(fps), "scanned": count,
                "seconds": round(time.time() - t0, 1), "synced": siblings}

    task = asyncio.ensure_future(_run())
    _BUILDING[target_id] = task
    try: return await task
    finally: _BUILDING.pop(target_id, None)


def scan_db2_bg(client, target_id, db2_id, done_cb=None):
    async def _bg():
        try:
            res = await scan_db2(client, target_id, db2_id)
            if done_cb: await done_cb(res)
        except Exception as e:
            log.exception("dedup: background DB2 scan failed for target %s", target_id)
            try:
                await _dm_admins(f"⚠️ DB2 duplicate-index scan FAILED for target "
                                 f"{target_id}: `{e}`\nScraping continues UNGATED — "
                                 f"rebuild with /dupescan.")
            except Exception: pass
    asyncio.ensure_future(_bg())
