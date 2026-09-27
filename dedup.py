"""dedup.py — v45: DB2-cover fingerprint index + duplicate-skip gate.

WHY: the same story gets re-posted across target channels (or re-posted later
in the same channel). Without a gate, the scraper re-runs the WHOLE chain
(Download -> short-link -> bypass -> media bot -> DB bundle + DB2 mirror) for
a post whose content is already archived — wasted time, wasted flood budget,
and a duplicate bundle in DB/DB2.

HOW: DB2 captions are the CLEANEST copy (botapi's _clean_caption has already
stripped every URL/@mention/avoid-string), so we fingerprint ONLY DB2 cover
posts (m.photo only — the cover is the post's identity; videos/.srt captions
are ignored, same gate as /scan4duplicates) and compare each INCOMING target
cover against that index before process_post ever runs.

DESIGN (locked with the owner):
  * per-target index  — doc _id = target_id. Two targets that SHARE one DB2
    channel each load their own in-memory copy (built from the same DB2);
    a cover already in the shared DB2 is skipped for BOTH targets.
  * ONE-TIME scan per target — triggered automatically after the /target
    wizard (background task) or manually via /dupescan <n>. /pause, /resume
    and restarts NEVER re-scan: the built index lives in Mongo + RAM.
  * AUTO-GROW — every cover that passes the gate and is scraped successfully
    gets its fingerprint pushed into the index, so a repeat of a NEW post is
    caught later without any DB2 re-scan.
  * MATCH — exact fingerprint equality first; on a miss, rapidfuzz
    token_set_ratio >= DUP_THRESHOLD (90) over the target's cached prints.
    Exact hits short-circuit with score 100.
  * NOTIFY — skips are batched: one DM per target after 10 skips, plus a
    mandatory flush whenever that target is paused via /pause <n> (or /pause
    all). /status shows the live per-target dupe counter.
"""
import asyncio
import logging
import re
import time
import unicodedata

import db as DB

log = logging.getLogger("dedup")

# rapidfuzz is ~20x faster than difflib at scale and tolerates word-order
# shifts; requirements.txt pins it. Fall back to difflib so the process still
# starts if the wheel is ever missing (e.g. someone deploys without rebuild).
try:
    from rapidfuzz.fuzz import token_set_ratio as _ratio
except Exception:  # pragma: no cover - safety net only
    import difflib
    def _ratio(a, b):
        return difflib.SequenceMatcher(None, a, b).ratio() * 100

DUP_THRESHOLD = 90.0        # owner-approved: 90% or better = duplicate
DUP_BATCH = 10              # DM the skip batch after this many per target
FP_MIN_TOKENS = 3           # shorter fingerprints false-positive on everything
FP_CAP = 20000              # max fingerprints kept per target doc ($slice)
FP_MAX_KEEP = 60000         # in-memory cap (RAM guard for huge channels)

# ---- state (populated lazily on the running loop; Py3.14-safe) ----
_FPS = {}            # target_id -> list[str]  (normalized fingerprints)
_INDEXED = set()     # target ids whose initial DB2 scan finished
_BUILDING = {}       # target_id -> asyncio.Task (background scan in flight)
_LOCKS = {}          # target_id -> asyncio.Lock (per-target index mutation)
_PENDING_SKIPS = {}  # target_id -> [skip dicts awaiting the batch DM]
_COUNTS = {}         # target_id -> total skips this session (for /status)


def _lock(target_id):
    if target_id not in _LOCKS:
        _LOCKS[target_id] = asyncio.Lock()
    return _LOCKS[target_id]


def _norm(text):
    """Same NFKD fold scraper.norm uses — bold/quote/mono/fancy fonts
    (𝗯𝗼𝗹𝗱, 𝘪𝘵𝘢𝘭𝘪𝘤, ｆｕｌｌｗｉｄｔｈ) all collapse to plain text."""
    if not text:
        return ""
    return unicodedata.normalize("NFKD", text).lower()


# metadata bullets / lines that must NOT be part of the fingerprint — mirrors
# botapi._NONSTORY_LINE_RE (kept local so dedup.py is self-contained).
_NONSTORY_RE = re.compile(
    r"(?:^|\b)(?:episode|subtitles?|censor(?:ship|ed)?|rating|network|audio|"
    r"quality|resolution|size|duration|genre|genres|studio|release|language|"
    r"source|seed|leech)\s*[:\-]", re.I)


def fingerprint_cover(caption):
    """Cover caption -> normalized fingerprint string (Cut C: recommended cut).

    1) cut at the FIRST '➪' bullet (the metadata block) — or at the first
       metadata-keyword line when the channel omits the bullet;
    2) NFKD-fold + lowercase (fancy-font captions match clean DB2 captions);
    3) drop zero-width chars, URLs, @mentions (target captions carry them,
       DB2 captions don't — stripping both sides makes them comparable);
    4) tokenize to word chars; drop tokens <3 chars and pure numbers;
    5) join with single spaces.
    Returns "" for empty/noise captions — those are never indexed and never
    gate-skipped (empty fingerprint == no information, so scrape normally).
    """
    if not caption:
        return ""
    head = caption
    if "➪" in head:
        head = head.split("➪", 1)[0]
    else:
        # no bullet marker — stop at the first metadata-style line instead
        kept = []
        for ln in head.splitlines():
            if _NONSTORY_RE.search(ln):
                break
            kept.append(ln)
        head = "\n".join(kept)
    s = _norm(head)
    s = re.sub("[\u200b-\u200f\ufeff]", "", s)     # zero-width chars
    s = re.sub(r"(?:https?://)?\S*(?:t\.me|telegram\.me)/\S+|https?://\S+",
               " ", s)                      # URLs (t.me + http)
    s = re.sub(r"@[A-Za-z0-9_]+", " ", s)   # @mentions
    toks = re.findall(r"\w+", s)
    toks = [t for t in toks if len(t) >= 3 and not t.isdigit()]
    return " ".join(toks)


# ---------------- Mongo-backed index (per target) ----------------

async def get_cover_fp(target_id):
    return await DB.db().cover_fp.find_one({"_id": str(target_id)})


async def init_cover_fp(target_id, db2_id, fingerprints, scanned_count,
                        build_seconds):
    """One-time load after a DB2 scan. Overwrites any previous index for this
    target (the scan is authoritative)."""
    await DB.db().cover_fp.update_one(
        {"_id": str(target_id)},
        {"$set": {"target_id": target_id,
                  "db2_id": db2_id,
                  "scanned_at": time.time(),
                  "scanned_count": scanned_count,
                  "index_build_time": build_seconds,
                  "fingerprints": [
                      {"fp": fp, "first_seen_msg": mid, "hits": [mid],
                       "added_at": time.time()}
                      for fp, mid in fingerprints]}},
        upsert=True)


async def add_cover_fp(target_id, fp, msg_id):
    """Auto-grow: record a successfully-scraped cover so a future repost is
    caught. Deduplicated ($ne precheck), newest-first, capped at FP_CAP."""
    if not fp:
        return False
    res = await DB.db().cover_fp.update_one(
        {"_id": str(target_id), "fingerprints.fp": {"$ne": fp}},
        {"$push": {"fingerprints": {
            "$each": [{"fp": fp, "first_seen_msg": msg_id, "hits": [msg_id],
                       "added_at": time.time()}],
            "$position": 0,
            "$slice": -FP_CAP}},
         "$setOnInsert": {"target_id": target_id}},
        upsert=True)
    return bool(res.modified_count or res.upserted_id)


async def get_db2_index(db2_id):
    return await DB.db().db2_cover_index.find_one({"_id": str(db2_id)})


async def set_db2_index(db2_id, last_msg_id):
    await DB.db().db2_cover_index.update_one(
        {"_id": str(db2_id)},
        {"$set": {"last_indexed_msg_id": last_msg_id,
                  "last_scan_ts": time.time()}},
        upsert=True)


# ---------------- in-memory cache + matching ----------------

async def _load_from_mongo(target_id):
    doc = await get_cover_fp(target_id)
    fps = [e["fp"] for e in (doc or {}).get("fingerprints") or []]
    _FPS[target_id] = fps[:FP_MAX_KEEP]
    return bool(fps)


async def ensure_index(target_id):
    """Guarantee _FPS[target_id] is populated (Mongo -> RAM), at most once
    per process per target. Returns True if the target has ANY prints."""
    if target_id in _FPS:
        return bool(_FPS[target_id])
    async with _lock(target_id):
        if target_id in _FPS:          # someone else filled it while we waited
            return bool(_FPS[target_id])
        return await _load_from_mongo(target_id)


async def find_dup(target_id, sig):
    """(match_fp, score) or None. Exact equality short-circuits at 100;
    otherwise best token_set_ratio >= DUP_THRESHOLD wins."""
    if not sig:
        return None
    if not await ensure_index(target_id):
        return None
    fps = _FPS.get(target_id) or []
    if sig in fps:
        return sig, 100.0
    best, best_fp = 0.0, None
    for fp in fps:
        r = _ratio(sig, fp)
        if r > best:
            best, best_fp = r, fp
    if best >= DUP_THRESHOLD:
        return best_fp, round(best, 1)
    return None


async def remember(target_id, fp, msg_id):
    """Cache a new fingerprint in RAM + Mongo after a successful scrape."""
    if not fp:
        return
    async with _lock(target_id):
        lst = _FPS.setdefault(target_id, [])
        if fp in lst:
            return
        lst.insert(0, fp)                       # newest first
        del lst[FP_MAX_KEEP:]
    await add_cover_fp(target_id, fp, msg_id)


# ---------------- duplicate-skip gate (called from bot.py) ----------------

async def already_indexed(target_id, caption):
    """The gate. Returns (match_fp, score) when this cover is already in the
    DB2 index (skip it), else None. Never raises — a fingerprint failure must
    NEVER block scraping."""
    try:
        return await find_dup(target_id, fingerprint_cover(caption))
    except Exception as e:
        log.warning("dedup: gate error on target %s (%s) — scraping anyway",
                    target_id, e)
        return None


async def note_scraped(target_id, caption, target_msg_id):
    """Auto-grow hook after a cover was successfully delivered."""
    try:
        await remember(target_id, fingerprint_cover(caption), target_msg_id)
    except Exception as e:
        log.warning("dedup: remember failed (%s)", e)


# ---------------- batched skip notifications ----------------

def _count(target_id):
    return _COUNTS.get(target_id, 0)


def status_lines():
    """Per-target dupe counters for /status (empty string when none)."""
    if not _COUNTS:
        return ""
    out = ["🧬 Duplicates skipped (this session):"]
    for tid, n in sorted(_COUNTS.items()):
        out.append(f"  • {tid}: {n}")
    return "\n" + "\n".join(out)


async def _record_skip(target_id, msg_id, score, match_fp):
    entry = {"msg_id": msg_id, "score": score,
             "match_fp": (match_fp or "")[:120], "ts": time.time()}
    q = _PENDING_SKIPS.setdefault(target_id, [])
    q.append(entry)
    _COUNTS[target_id] = _COUNTS.get(target_id, 0) + 1
    if len(q) >= DUP_BATCH:
        await flush_skips(target_id)


async def _dm_admins(text):
    """Batch DM via the control bot, falling back to the userbot (same
    pattern as flow._alert_admins, kept local to avoid an import cycle)."""
    from flow import state
    from config import ADMIN_USER_ID
    import botapi
    ctl = getattr(botapi, "bot", None)

    async def _one(uid):
        if ctl is not None:
            try:
                await ctl.send_message(uid, text, parse_mode="md")
                return True
            except Exception:
                pass
        try:
            c = getattr(state, "scrape_client", None)
            if c is not None:
                await c.send_message(uid, text, parse_mode="md")
                return True
        except Exception:
            pass
        return False

    sent = False
    if ADMIN_USER_ID:
        sent = await _one(ADMIN_USER_ID) or sent
    for a in await DB.get_admins():
        if a != ADMIN_USER_ID:
            sent = await _one(a) or sent
    if not sent:
        log.warning("dedup: skip batch could not be delivered: %s", text[:120])


async def flush_skips(target_id, reason="batch of %d" % DUP_BATCH):
    """DM the pending skip batch for one target (no-op when empty). Called
    automatically at DUP_BATCH and on /pause of that target."""
    q = _PENDING_SKIPS.get(target_id) or []
    if not q:
        return
    _PENDING_SKIPS[target_id] = []
    lines = [f"📋 DUPLICATES SKIPPED — target {target_id} ({reason})"]
    for s in q:
        lines.append(f"• post {s['msg_id']} — {s['score']}% match in DB2")
    fp = q[-1]["match_fp"]
    if fp:
        lines.append(f"last match: {fp}…")
    lines.append(f"total dupes this session: {_count(target_id)}")
    try:
        await _dm_admins("\n".join(lines))
    except Exception as e:
        log.warning("dedup: flush_skips DM failed (%s)", e)


async def record_skip(target_id, msg_id, score, match_fp):
    """bot.py calls this for every gated skip; never raises."""
    try:
        await _record_skip(target_id, msg_id, score, match_fp)
        await DB.incr("dupes_skipped")
    except Exception as e:
        log.warning("dedup: record_skip failed (%s)", e)


# ---------------- one-time DB2 scan ----------------

def is_indexed(target_id):
    return target_id in _INDEXED


def invalidate(target_id):
    """Force the next scan_db2 to rebuild this target (used by /dupescan)."""
    _INDEXED.discard(target_id)
    _FPS.pop(target_id, None)


async def scan_db2(client, target_id, db2_id, progress_cb=None):
    """ONE-TIME scan of a target's DB2: fingerprint every cover post
    (m.photo only — videos/.srt ignored) and load Mongo + RAM.

    Idempotent: a completed scan is never repeated (re-run with /dupescan
    which forces a rebuild). Runs as a background task; yields every 100
    fetched messages so the scrape loop is never starved (same throttle
    /scan4duplicates uses)."""
    if target_id in _INDEXED and _FPS.get(target_id):
        return {"status": "cached", "count": len(_FPS[target_id])}
    tsk = _BUILDING.get(target_id)
    if tsk and not tsk.done():
        return {"status": "already_running"}
    async def _run():
        t0 = time.time()
        fps, count, covers = [], 0, 0
        async for m in client.iter_messages(db2_id):       # newest -> oldest
            count += 1
            if count % 100 == 0:
                await asyncio.sleep(0.1)
                if progress_cb:
                    try:
                        await progress_cb(count, covers)
                    except Exception:
                        pass
            if not m.photo:                                # cover posts only
                continue
            sig = fingerprint_cover(m.message or "")
            if not sig:
                continue
            covers += 1
            fps.append((sig, m.id))
        await init_cover_fp(target_id, db2_id, fps, count,
                            round(time.time() - t0, 1))
        await set_db2_index(db2_id, fps[0][1] if fps else 0)
        async with _lock(target_id):
            _FPS[target_id] = [fp for fp, _ in fps][:FP_MAX_KEEP]
        _INDEXED.add(target_id)
        log.info("dedup: target %s indexed — %d cover fingerprint(s) from "
                 "DB2 %s (%d msgs scanned in %.1fs)",
                 target_id, len(fps), db2_id, count, time.time() - t0)
        return {"status": "done", "count": len(fps), "scanned": count,
                "seconds": round(time.time() - t0, 1)}
    task = asyncio.ensure_future(_run())
    _BUILDING[target_id] = task
    try:
        return await task
    finally:
        _BUILDING.pop(target_id, None)


def scan_db2_bg(client, target_id, db2_id, done_cb=None):
    """Fire-and-forget wrapper for the /target wizard: builds in the
    background and DMs the result when done. Never blocks the wizard reply."""
    async def _bg():
        try:
            res = await scan_db2(client, target_id, db2_id)
            if done_cb:
                await done_cb(res)
        except Exception as e:
            log.exception("dedup: background DB2 scan failed for target %s",
                          target_id)
            try:
                await _dm_admins(
                    f"⚠️ DB2 duplicate-index scan FAILED for target "
                    f"{target_id}: `{e}`\nScraping continues UNGATED — "
                    f"rebuild with /dupescan.")
            except Exception:
                pass
    asyncio.ensure_future(_bg())
