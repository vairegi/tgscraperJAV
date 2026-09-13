TGSCRAPER v18 PATCH — 6 changed files (overwrite, push, redeploy)
=================================================================
  flow.py   — FlowState gains paused_ids (in-memory mirror of per-target
              pause flags, synced from Mongo each pass by bot.py).
  botapi.py — /pause <n> & /resume <n> (per-target), menu + /help updated,
              /status shows individually paused targets, /cancel listed.
  bot.py    — scrape loop skips individually-paused targets; per-target
              pause flags mirrored from Mongo every pass; a pass aborts if
              its channel gets paused mid-flight; /progress marks ⏸PAUSED.
  db.py     — targets now carry a persisted "paused" flag + new
              set_target_paused(); auto-migrates old records (default False).
  README.md — command table updated for /pause [n], /resume [n], /lastpost [n].

WHY: you added a 2nd target and /resume scraped the OLD channel — with no
per-target control the loop only ever worked the oldest-progress channel.
Now: /pause 1 pauses ONLY target 1, /resume 2 resumes ONLY target 2,
bare /pause /bare /resume still affect EVERYTHING (unchanged). Target
numbers are the numbers shown by /targets. Flags persist in MongoDB —
they survive Render crashes/restarts exactly like progress does. The new
commands are in the tappable menu AND /help (and /cancel, which existed
but was never listed, is now registered too).

NOT changed (from your log — these are external, not bugs):
  - ChannelPrivateError on posts 166-170: the userbot account lost access
    to the BYPASS group (-1003563519821) — banned, removed, or the group
    migrated. Fix on Telegram's side: re-join/re-add the account (with
    POST permission), or point /bypass at a new group.
  - "no matching reply in bypass group within 60s" on posts 161-165: the
    bypass bot didn't answer. Raise WAIT_BYPASS_REPLY in Render env
    (e.g. 120) if it stays slow. Same for WAIT_BOT_REPLY (Fubuki, 20s).
  Both now fail loudly in /progress instead of looping silently.

TESTS (sandbox, mock Telethon/Motor — no network):
  - py_compile all 10 files: OK
  - db target pause/unpause, unknown-target -> None, legacy-record
    migration: PASS
  - botapi handlers driven via list_event_handlers with FakeEv:
    /pause 2 pauses only target 2 (reply + Mongo flag + paused_ids);
    /resume 2 resumes only it; /pause 9 rejected with listing; bare
    /pause global; bare /resume clears global + all per-target flags;
    /help lists pause/resume/cancel; menu registration includes
    pause/resume descriptions. 26 PASS / 0 FAIL.
  - scrape-loop target selection with one paused (async integration
    against the real loop) NOT runnable in sandbox without live Mongo —
    logic verified by unit tests on the selection predicate instead.

AFTER DEPLOY: /targets shows numbered channels; try /pause 2 then
/progress — target 2 shows ⏸PAUSED and the loop keeps scraping target 1.
