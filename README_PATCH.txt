================================================================================
README_PATCH — v26: MTProto bulk jobs (mass delete / forward / add-bot-admin)
================================================================================

WHAT CHANGED (drag these files onto the repo root, replacing existing ones):

  mtprotomgr.py   NEW FILE — the three bulk-job engines (background workers,
                  pacing, flood handling, scraper auto-pause, Mongo cursor).
  botapi.py       CHANGED — 8 new commands added to _CMDS (so the "/" menu
                  AND /help both list them — they can never drift) + their
                  handlers. Nothing existing was touched.
  config.py       CHANGED — 3 new optional env vars (safe defaults, no
                  Render change needed):
                    MASS_DELETE_CHUNK = 100  (ids per delete call)
                    MASS_DELETE_DELAY = 3    (seconds between chunks)
                    FORWARD_DELAY     = 3    (seconds between copies)
  README.md       CHANGED — documents the new commands.

NEW COMMANDS (all in the "/" menu and /help):

  /massdlt <chat_id> <start_link> <end_link>
      Deletes EVERY message between the two message links (inclusive).
      Telegram-safe: messages go out in chunks of 100 ids with ~3s rests
      between chunks (+0-1.5s jitter), so a 2000-message range is deleted
      piece-by-piece — never one giant burst. FloodWait is slept through
      in place and the same chunk retried (cap BULK_MAX_FLOOD=900s).
  /massdlt_status   live progress (deleted/total, floods slept)
  /massdlt_stop     cooperative stop after the current chunk

  /forward <target_channel> <source_channel> <start_link> <end_link>
      Copies every message in the range into the target channel BY
      REFERENCE (send_file with msg.media) — NO "Forwarded from" tag,
      ZERO downloads (same proven mechanism as the DB delivery). Media,
      text and buttons are preserved; one message every ~3s.
  /forward_status   live progress
  /forward_stop     stops — the cursor is saved in MongoDB after EVERY
                    message, so nothing is lost
  /forward_resume   continues a stopped/interrupted/crashed forward from
                    the exact next message (survives Render restarts)

  /add <channel_id> @bot1 [@bot2 @bot3 ...]
      The USERBOT adds each bot to the channel as ADMIN with ALL rights
      (post, edit, delete, ban, invite, pin, add-admins, manage-call).
      Non-bot usernames are skipped with a note. The userbot must already
      be an admin with add-admins permission in that channel.
      Example: /add -1002392274488 @loverxnbot @loverxn1bot @loverxn2bot

SAFETY (same design as v25/v25.1 bulk editing):
  - While any bulk job runs, the SCRAPER auto-pauses (Telegram's flood
    bucket is account-wide) and auto-resumes when the job finishes.
  - FloodWait mid-run = slept through in place, same work retried — no
    error ever escapes mid-run.
  - A summary is DM'd to ADMIN_USER_ID when a run finishes.
  - The control bot stays responsive during long runs (jobs are background
    tasks).

TESTING (sandbox, mocks — no live Telegram session available):
  - py_compile: ALL 10 project files PASS.
  - Behavior tests (SimpleNamespace + AsyncMock): 25 PASS, 0 FAIL —
    link parsing, massdlt range delete (inclusive, chunked), massdlt stop
    mid-run, forward range copy by reference, cursor cleared on completion,
    forward_resume continues from saved cursor, forward_status reporting,
    /add grants full ChatAdminRights and skips non-bots, all 8 commands
    present in _CMDS ("/" menu + /help).
  - NOT tested against live Telegram (no session in sandbox) — the first
    real /massdlt on a big range is worth watching in Render logs.

DEPLOY: drag this zip's contents onto the repo root in the GitHub web UI;
Render auto-redeploys. If the "/" menu looks stale afterwards, close and
reopen the control-bot chat (Telegram caches the menu).
================================================================================
