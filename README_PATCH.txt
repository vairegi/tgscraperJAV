TGSCRAPER v21 PATCH — 4 changed files (overwrite, push, redeploy)
=================================================================
  forwarder.py — delivery VERIFICATION: silently-dead sends are detected,
                 the empty message is removed, source refetched, send
                 retried; every delivery logs its DB message id; text-only
                 messages are now delivered too (full mirror).
  flow.py      — _collect_media collects EVERYTHING the media bot sends
                 (videos + srt + photos + stickers + text notes); only the
                 userbot's own '/start' trigger message is excluded.
  README.md    — documents verification + full mirroring.
  README_PATCH.txt — this file.

THE BUG (from your screenshots + log): post 163's log said
"collected 8 msg(s): 3 video + 5 other" then "post 163 done" with NO
error — yet an hour later the DB channel had only the cover + decorations.
That combination means Telegram SILENTLY ACCEPTED the video sends: when
a file reference is dead server-side, send_file can return normally but
the created message carries no media, so it never renders. Photos and
stickers had live references (landed); the 3 video references were dead
(invisible). Nothing failed, so nothing was retried — until now.

FIX 1 — verified sends: after every send_file, the returned message is
checked: no media = dead reference. The empty shell message is deleted
from the DB channel, the source message is refetched from the media bot's
chat (fresh file reference), and the send retried (3 attempts, 5s apart).
If it still can't be delivered, the failure is raised and logged in Mongo
(/progress) instead of being silently swallowed. Every delivered message
logs "delivered msg N -> DB msg M" so you can verify by eye in Render logs.

FIX 2 — full mirror (your request): the DB channel now receives EVERYTHING
the media bot sends — videos, stickers, photos AND text-only messages
(e.g. Hilda's "This File is deleting automatically in 12 hours" notice).
The only thing excluded is the userbot's own '/start' trigger.

STILL: zero downloads, zero temp files, no "Forwarded from" tag, all video
attributes (duration, resolution, thumbnail, streaming, filename, spoiler)
preserved via server-side reference copy.

TESTS (sandbox, mock Telethon — no network):
  - py_compile all 10 files: OK
  - 22 behavior tests: healthy send untouched; DEAD-REFERENCE send ->
    empty shell deleted + refetch + retry with fresh media (the exact
    bug scenario); refetch-empty raises for /progress; expired-reference
    path intact; text-only delivered as text; mixed batch order kept;
    collector mirrors text notes + excludes '/start' + keeps video gate.
    22 PASS / 0 FAIL.
  - NOT live-tested (no session in sandbox). First post after deploy,
    watch for "delivered msg N -> DB msg M" lines — one per message.

AFTER DEPLOY: re-run the Hilda post:
  /goto <that post's link>  then  /start
The DB channel should get cover + decorations + text notice + all 3
videos (480p/720p/1080p), each with its delivery logged.
