TGSCRAPER v25 PATCH — 4 changed files (overwrite, push, redeploy)
=================================================================
  botapi.py  — /replace + /deletetext rebuilt as a PACED BACKGROUND worker.
  config.py  — new env knobs: BULK_EDIT_DELAY, BULK_MAX_FLOOD,
               BULK_PROGRESS_EVERY.
  README.md  — documents the pacing behavior.
  README_PATCH.txt — this file.

WHY (your run): /replace found 43 matching messages and fired 43 edits
back-to-back — Telegram answered with FloodWaitError ("A wait of 182
seconds is required"), and 34 edits were lost to the flood. With 500+
posts to fix, unpaced editing is unusable.

WHAT CHANGED:
  1. SCAN FIRST, EDIT SLOW: all matching messages are collected up front
     (server-side search), then a background worker edits ONE message
     every BULK_EDIT_DELAY seconds — default 2.5s (you asked for 2–3s).
     Telegram's edit rate limit is never hit.
  2. FLOOD-PROOF: if Telegram still answers FloodWaitError, the worker
     SLEEPS the exact wait + 5s and retries the SAME message — nothing is
     skipped. Waits longer than BULK_MAX_FLOOD (default 900s) count as a
     failure instead of parking forever.
  3. LIVE UPDATES: you get "📋 Found N message(s)… ETA ~X min"
     immediately, then a progress line every 10 edits, a notice for every
     flood wait slept through, and a final summary:
     "✅ Done — Title: edited 43/43 in 2.1 min, 1 flood wait(s) slept
      through" (plus the real error if anything failed).
  4. BACKGROUND: the worker runs as a task — the control bot answers other
     commands while a long edit run is in progress.
  5. /deletetext empty-message behavior unchanged (text-only posts with
     nothing left are deleted; media posts get caption cleared).

ENV KNOBS (Render dashboard, no redeploy):
  BULK_EDIT_DELAY=2.5     seconds between edits (set 2 or 3 as you like)
  BULK_MAX_FLOOD=900      max flood wait to sleep through
  BULK_PROGRESS_EVERY=10  progress update cadence

TESTS (sandbox, mock userbot — no network):
  - py_compile all 10 files: OK
  - 5-message run: all edited, pacing applied, progress updates shown,
    summary correct: PASS
  - FloodWait mid-run: slept through (measured >=6s), SAME message
    retried, flood notice + counted in summary: PASS
  - Flood beyond cap: counted as failure, NO 30-min park: PASS
  - /deletetext empty-delete path intact under pacing: PASS
  - no matches: clean 'Nothing found', no worker spawned: PASS
  - 17 PASS / 0 FAIL. NOT live-tested against Telegram (no session).

AFTER DEPLOY: re-run
  /replace -1004369767119 "@VixinCult" "@NSFW_Universe"
Expected: "Found 43 message(s)… ETA ~1.8 min" -> progress lines ->
"edited 43/43". The 34 messages lost last time will be fixed in this run
(the scan re-finds them because they still contain @VixinCult).
