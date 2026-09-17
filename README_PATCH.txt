TGSCRAPER v25.1 PATCH — 4 changed files (overwrite, push, redeploy)
==================================================================
  botapi.py    — bulk-edit worker: scraper AUTO-PAUSE during edits,
                 jittered pacing (2.5s + 0–1.5s random), resume notice.
  forwarder.py — MediaInvalidError (dead file reference rejected at send
                 time) now gets refetch-and-retry like FileReferenceExpired.
  README.md    — documents auto-pause + jitter.
  README_PATCH.txt — this file.

WHY (your question): the pacing WAS working — your own 7:27 run proves it:
"edited 9/9 in 0.4 min" = 24s for 9 edits = ~2.7s each. The 7:28 timestamp
on the flood notice is the status message's ORIGINAL send time — Telegram
keeps that on edits; the notice itself went out ~2 min later (33 edits ×
~2.75s ≈ 91s + flood). The real flood cause was CONCURRENCY: the scraper
was delivering post 358 on the SAME userbot account while the edit worker
ran — Telegram's flood bucket is account-wide, so 1 edit/2.5s + scraper
sends together tripped the limit.

FIX:
  1. While a /replace or /deletetext worker runs, the scraper auto-pauses
     (queue message says so) and auto-resumes when it finishes, replying
     "▶️ Scraper resumed — bulk edit finished." Nothing to manage manually.
  2. Pacing is now jittered (BULK_EDIT_DELAY + random 0–1.5s) — no fixed
     burst pattern for Telegram's heuristics.
  3. Bonus fix for the error visible at the top of your screenshot:
     "The provided media object is invalid (caused by SendMediaRequest)"
     = a dead file reference REJECTED at send time (cousin of the silent
     dead-reference case). forwarder.py now catches MediaInvalidError and
     refetches + retries, same as expired references — post 358's cover
     will self-heal on re-run.

TESTS (sandbox, mock userbot — no network):
  - py_compile all 10 files: OK
  - scraper auto-pauses during run, auto-resumes after with notice;
    no auto-pause when scraper already paused; jittered pacing verified;
    MediaInvalidError -> refetch + retry delivers; forwarder regression
    (healthy send untouched). 9 PASS / 0 FAIL.
  - NOT live-tested against Telegram (no session in sandbox).

AFTER DEPLOY: re-run the 99-message command:
  /replace -1004369767119 "𝟭𝟴+ 𝗡𝗲𝘁𝘄𝗼𝗿𝗸: @𝗖𝘂𝗹𝘁𝘂𝗿𝗲𝗱_𝗔𝗹𝗹𝗶𝗮𝗻𝗰𝗲" "𝟭𝟴+ 𝗡𝗲𝘁𝘄𝗼𝗿𝗸: @NSFW_Universe"
Expect: Found N -> auto-pause note -> progress lines -> "edited N/N" ->
"▶️ Scraper resumed". With the scraper paused, floods should be rare —
and any that happen are slept through with nothing lost.
Also re-run post 358 for the failed cover: /goto <post 358 link>, /start.
