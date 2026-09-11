TGSCRAPER v3.2 PATCH — 3 changed files only
===========================================
Overwrite these files in your repo root, push, and Render redeploys:

  1. bot.py     — FIX (MAIN): scraper now runs ONLY after /start (new
                  state.started gate) — before, /start didn't actually arm
                  the loop. FIX: userbot command replies DISABLED (bot-only
                  replies, no more double answers). FIX: verbose Render logs
                  (scraper ACTIVE / POST FOUND / post N done / scan pass
                  complete / waiting config) so the log is never silent.
                  Catch-up pass now polls every 30s instead of log-spamming.
  2. botapi.py  — FIX: /start refuses with a clear message if target/bypass/
                  db not set. /stop fully stops (started=False). Wizard no
                  longer eats your next /command as an ID.
  3. flow.py    — adds state.started flag.

USAGE (control bot @scrapjavbot):
  /ping -> /target -> send id -> /bypass -> send id -> /adddb -> send id
  -> /start -> watch Render logs + /progress

NOTE: Mongo progress from the old buggy run may be polluted (e.g. if the
wizard ever swallowed "/start" as a text value). If scraping looks stuck at
a weird message id, just tell me — one command (/reset) can clear it, or
delete the 'progress' collection in Atlas.
