
================================================================================
README_PATCH — v42: caption download links + /keepimages + restricted-content
cover workaround + /targets board TTL/invalidation
================================================================================

DRAG onto the repo root: bot.py, flow.py, scraper.py, db.py, botapi.py,
richboard.py, forwarder.py, README.md, README_PATCH.txt
(everything else unchanged — forwarder.py IS in this zip because the
restricted-cover workaround lives in send_cover).

1) EMBEDDED CAPTION LINK SUPPORT. The /target wizard now asks (after DB and
   DB2): "Is the download link in a 'Button' or in the 'Caption'?" Caption
   mode asks for the EXACT text holding the hidden hyperlink (e.g. "Download
   Here") and stores link_mode + link_trigger per target in MongoDB
   (db.get_targets auto-migrates old docs to mode 'button'). Detection and
   scraping are mode-aware:
   - scraper.is_post(msg, link_mode, link_trigger) — caption mode = media +
     caption + the trigger text carrying a MessageEntityTextUrl (no buttons
     needed); button mode is byte-identical behavior to before.
   - scraper.find_caption_url() matches the entity's covered text EXACTLY
     after the same NFKD Unicode fold the button matcher uses, so
     '𝗗𝗼𝘄𝗻𝗹𝗼𝗮𝗱 𝗛𝗲𝗿𝗲' == 'Download Here'.
   - flow.process_post caption path: a t.me/<bot>?start=… caption link runs
     the SAME deep-link -> Short-link -> bypass chain as a Download button;
     any other URL is treated as the short link itself and jumps straight
     into _bypass_chain (link_bot may be None there — the bypass result
     resolves it; the group path's base_f moved AFTER the Open-link click
     for exactly this reason).
   - NEW COMMAND /targatelinkmode <n> button | caption <trigger text> —
     change an existing target's mode without re-adding it (progress kept).

2) /keepimages on|off — GLOBAL Mongo-backed switch (default ON = old
   behavior). OFF: flow's classification discards EVERY image the media bot
   sends (photos AND image-mime documents, with or without caption) before
   the DB bundle is built. Videos, .srt and text notes are never touched;
   the video-armed quiet timer in _collect_media is unchanged.

3) RESTRICTED TARGETS ("Restrict saving content" / msg.noforwards).
   forwarder.send_cover detects the flag and, instead of a by-reference
   copy (which Telegram rejects), downloads the cover to a temp file,
   uploads it to the DB channel as a NEW message with the exact original
   caption/buttons/spoiler, then DELETES the temp file in a finally block —
   success or failure — so Render's disk never fills. Media from the bypass
   bot is unaffected (it isn't restricted), and the fresh DB cover post
   mirrors to DB2 normally.

4) /targets BOARD LIFECYCLE. BOARD_TTL 150s -> 1800s (30 min). mark_board
   now ZEROES the previous board's timestamp when a new board is sent for
   the same chat (deleting it would fall through to the live-board check
   and wrongly allow stale taps), so buttons on an older scrolled-up board
   answer "This board has expired" and change nothing. In-place edits
   (Refresh/toggle) pass the same message id and are never invalidated.

TESTING: py_compile PASS on all 7 changed .py files. Mocked sandbox suite
(55+ assertions): caption URL extraction (exact/fancy-font/wrong-trigger/
no-entity), mode-aware is_post/why_not_post, is_image_msg, /keepimages
on/off classification, caption-mode process_post end-to-end (deep-link AND
direct-short-link forms) with a mocked client, button-mode process_post
regression, restricted cover download->upload->temp-deleted (plus deletion
on upload failure, and the normal non-restricted path never downloading),
board TTL=1800 + previous-board invalidation + in-place edit survival, db
target migration/add/set_target_link_mode/keep_images defaults, and botapi
handler registration + admin-gate + wizard-step wiring. NO live Telegram
calls were made — validate one real caption-mode post on a test target
after deploy.
================================================================================

================================================================================
README_PATCH — v41: /stats v2 + /removeavoid N + replace-wins guarantee
================================================================================

DRAG onto the repo root: botapi.py, README_PATCH.txt (everything else unchanged).

1) /stats v2 — per userbot: connection state, name/@username/id, then a full
   membership matrix: ✅ member / ❌ NOT a member / 👑 admin for EVERY target
   channel and every DB + DB2 channel, plus the CONTROL BOT's own DB/DB2
   rights. A ❌ on a target is exactly why a resume silently does nothing —
   join it with /invite.

2) /removeavoid N — N is the entry number in the /avoidtext list (per-target
   avoids numbered top to bottom). Bare /removeavoid shows the numbered list.

3) /replaceword always wins over /avoid and /avoidtext — replacement rules
   run BEFORE any strip in the DB2 mirror pipeline (order verified in code).

TESTING: py_compile PASS on botapi.py; role-check and numbering logic match
patterns already proven in v38–v40.
================================================================================

================================================================================
README_PATCH — v45: DB2 duplicate-skip gate (fingerprint index + /dupescan)
================================================================================

DRAG onto the repo root: dedup.py (NEW), bot.py, botapi.py, requirements.txt,
README.md, README_PATCH.txt (everything else unchanged).

1) NEW MODULE dedup.py — fingerprint_cover() (story-only head up to the
   first ➪ metadata bullet, NFKD font fold so bold/quote/mono/fancy captions
   match their clean DB2 twins, URL/@mention strip, >=3-char tokens), Mongo
   collections cover_fp (per-target index, $slice-capped at 20000) and
   db2_cover_index (scan watermark), rapidfuzz token_set_ratio >= 90 matching
   with an exact-equality fast path, batched skip DMs (10 per target or on
   /pause), auto-grow after every successful scrape. The gate NEVER raises —
   a fingerprint error means "scrape normally", never a blocked post.
2) GATE in bot.py — both the sequential loop and the v39 parallel dispatcher
   call dedup.already_indexed() right after is_post(); a duplicate cover
   short-circuits the ENTIRE chain (no Download click, no bypass, no DB/DB2
   bundle) and advances progress exactly like a normal non-post skip.
3) ONE-TIME AUTO SCAN — the /target wizard and /setdb2 fire
   dedup.scan_db2_bg(): DB2 history is read ONCE by the USERBOT (bot
   accounts are blocked from GetHistoryRequest), cover posts only (m.photo
   gate — same rule as /scan4duplicates), fingerprints stored in Mongo and
   cached in RAM. /pause, /resume, /goto and restarts NEVER re-scan.
   /dupescan <n> forces a rebuild.
4) requirements.txt — adds rapidfuzz (dedup falls back to difflib if the
   wheel is ever missing).

TESTING: py_compile PASS on all 6 files. Mocked sandbox suite: fingerprint
cut at ➪ / metadata-keyword fallback / fancy-font NFKD fold / URL+mention
strip / empty-caption pass-through, exact + fuzzy >=90 matching,
below-threshold pass-through, $ne-dedup push, cover-only scan filtering
(photos in, videos/.srt/empty captions out), cached rescan no-op,
batch-of-10 DM + on-pause flush, gate-never-raises. NO live Telegram calls —
validate one real duplicate post on a test target after deploy.
================================================================================

================================================================================
README_PATCH — v45.1: Python-3.14 rapidfuzz pin + shared-DB2 index sync +
pause/resume skip details
================================================================================

DRAG onto the repo root: dedup.py, bot.py, botapi.py, requirements.txt,
README.md, README_PATCH.txt (everything else unchanged).

1) BUILD FIX — rapidfuzz pinned to >=3.14.6,<4 (cp314 manylinux wheels are
   published on PyPI from 3.14.6; 3.10.1 had none, forcing a failing
   scikit-build-core source build on Python 3.14). Remove the Render
   PYTHON_VERSION=3.11.9 override after deploy.
2) SHARED DB2 — targets pointing at the same DB2 channel share ONE
   fingerprint index: dedup.share_db2() copies a sibling's index (RAM +
   Mongo, marked 'shared_from') instead of rescanning; scan_db2() syncs
   every sibling on completion; remember() propagates every auto-added
   fingerprint to all siblings. /dupescan shares when a sibling index
   exists; a forced rebuild invalidates siblings so one rebuild refreshes
   the whole DB2 group. /target and /setdb2 share first, scan second.
3) PAUSE/RESUME DETAILS — /pause <n> appends the target's pending skip
   batch (post ids + % match) inline via dedup.consume_skips()/
   format_batch(); /resume <n> reports the target's session dupe total
   and clears any pending batch. Bare /pause still DMs every target's batch.

TESTING: py_compile PASS on all 13 files; 30-assertion mocked suite
(fingerprint cut/fold/strip, exact + fuzzy >=90, below-threshold pass,
$ne dedup, cover-only scan filtering, sibling share + scan sync + remember
propagation, cached rescan no-op, batch-of-10 DM, pause consume-vs-flush,
gate-never-raises on Mongo failure). NO live Telegram calls.
================================================================================
