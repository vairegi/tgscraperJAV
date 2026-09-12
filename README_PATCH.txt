TGSCRAPER v11 — MULTI-ACCOUNT ROTATION (3 changed files + gen_session.py)
=========================================================================
  session_manager.py — rewritten: loads ALL sessions, round-robin rotate().
  config.py          — STRING_SESSIONS="sess1,sess2,..." (comma-separated);
                       falls back to single STRING_SESSION unchanged.
                       POSTS_PER_ACCOUNT=20 (rotation quota, env-tunable).
  bot.py             — scrape_loop takes the SessionManager:
                       * after POSTS_PER_ACCOUNT posts -> rotate to next
                         account; the previous one rests while the next
                         CONTINUES FROM THE SAME Mongo progress (no overlap,
                         no re-scrape).
                       * on FloodWait -> rotate IMMEDIATELY; the flooded post
                         is retried by the fresh account. Single-account
                         mode keeps the old sleep/park behavior.
                       Log lines now show the account: "post 33 done
                       (acc1/2, 17/20 on this account)" and "ROTATE: acc1/2
                       rested -> now scraping with acc2/2".
  gen_session.py     — run locally to mint StringSessions for extra accounts.

SETUP (Render env):
  STRING_SESSIONS = <session_acc1>,<session_acc2>
  POSTS_PER_ACCOUNT = 20        (optional; default 20)
  (Keep STRING_SESSION as acc1 or remove it — STRING_SESSIONS wins.)

IMPORTANT: every account must be a MEMBER of the target channel AND the
bypass group (with message permission), and ADMIN (post rights) in the DB
channel — the rotating account does the clicking/sending/uploading.

NOTE: rotation rotates the SCRAPER account. The control bot (@scrapjavbot)
is unaffected. Both accounts messaging the SAME bypass group is fine, but
if the bypass group rate-limits per-group rather than per-account, consider
adding a second bypass group later — tell me and I'll wire rotation there too.
