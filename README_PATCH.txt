TGSCRAPER v17 PATCH — 2 changed files (overwrite, push, redeploy)
=================================================================
  forwarder.py — delivery rewritten: copy-mode BY REFERENCE, no downloads.
  README.md    — documents the new delivery (Crash resilience section).

WHY: v16 downloaded every video to a temp file on the Render disk and
re-uploaded it. Unnecessary — the userbot can deliver the cover post and
the media bot's files to the DB channel WITHOUT downloading anything.

WHAT CHANGED:
  - Every message is now re-sent with send_file(dbc, msg.media). Telethon
    reuses the message's existing Telegram file reference, so Telegram
    copies the file SERVER-TO-SERVER into the DB channel. The bytes never
    touch the Render disk or RAM — stays flat even for 700MB+ videos.
  - Still NO "Forwarded from" tag (this is a fresh send_file, never
    forward_messages). Caption, spoiler flag, inline buttons, playable
    video with thumbnail/duration/filename — all preserved exactly.
  - REMOVED completely: tempfile, download_media, _download, _thumb, and
    all filesystem cleanup logic (tmpdir creation, os.remove, os.rmdir).
  - NEW: if Telegram reports a file reference expired (can happen on OLD
    cover posts), the message is refetched from its source chat to get a
    fresh reference and retried automatically (3 attempts, 5s backoff);
    if it still fails, the error propagates so the failure is logged in
    Mongo with stage+reason (visible via /progress).
  - flow.py UNCHANGED — same send_cover / send_media calls, same order
    (cover post first, then videos + srt + other).

TESTS (sandbox, mock Telethon — no network):
  - py_compile forwarder.py + flow.py: OK
  - 26 behavior tests: 26 PASS, 0 FAIL
    (reference send with spoiler/caption/buttons kept; order preserved;
     zero download_media / forward_messages / temp-file usage;
     expired-reference refetch+retry; transient-failure backoff;
     unrecoverable failure raises for logging; text-only fallback)
  - NOT live-tested against Telegram (no session in sandbox) — the send
    path uses the same documented send_file API as before, minus the
    download/re-upload steps.

AFTER DEPLOY, watch Render logs for one post:
  "sending cover post to DB" -> "post N done" with no temp-file errors.
If a very old post ever logs "file reference expired ... refetching",
that is the new auto-recovery working, not an error.
