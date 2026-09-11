TGSCRAPER v3 PATCH — 2 changed files only
=========================================
Replace these two files in your repo root (overwrite existing):
  1. bot.py      — v3: FloodWait-safe startup (sleeps in-process instead of
                   crashing -> Render never restart-loops or re-spams Telegram
                   auth), clean task shutdown (no 'never awaited' warnings),
                   FloodWait during scraping retries the SAME post, Py3.14
                   event-loop creation.
  2. botapi.py   — v3.1: control-bot client now created LAZILY inside start()
                   (fixes Py3.14 'no current event loop in thread MainThread'
                   crash at import), /ping added, 13-command tappable menu.

All other files are unchanged from your current repo (commit eeaa01a).

Render env vars needed:
  API_ID, API_HASH, STRING_SESSION, BOT_TOKEN, ADMIN_USER_ID,
  MONGO_URI=mongodb+srv://whyithappenes_db_user:<password>@jav.toeac7k.mongodb.net/?retryWrites=true&w=majority

NOTE: your FloodWait is still active on Telegram's side (~33 min from the last
restart). After deploying, the bot will log "FloodWaitError — sleeping Ns
in-process" and simply wait it out. Do NOT manual-restart during that sleep.
