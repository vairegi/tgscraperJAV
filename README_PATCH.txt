================================================================================
README_PATCH — v31: echo-image skip REALLY fixed + DB2 mirror ordering
================================================================================

DRAG onto the repo root: flow.py, botapi.py, README_PATCH.txt
(bot.py / db.py / mtprotomgr.py / config.py / README.md unchanged from v30.)

1) BOT ECHO COVER-IMAGE — ACTUALLY FIXED THIS TIME (flow.py)
   The v30 filter checked m.photo — but spoiler cover images arrive as
   DOCUMENTS (m.photo is None), so it never matched and the echo still landed
   in the DB channel (the duplicate cover in your screenshot).
   v31: after videos and .srt are separated out, ANY remaining message with
   media (photo OR document image) whose caption shares >=80% of its words
   with the cover post's caption is skipped. Word-overlap (not exact string)
   so the bot's copy matches even if emoji/spacing differ slightly. Only
   images are ever skipped — a video with the same caption is ALWAYS kept.
   Still logged: "skipped N bot echo image(s)".

2) DB2 MIRROR ORDER — COVER FIRST, ALWAYS (botapi.py)
   Telethon dispatches channel NewMessage events CONCURRENTLY. When the cover
   copy was slow (large file / flood wait), the next message's copy finished
   first — DB2 got videos before the cover, uneven order.
   Fix: a per-DB-channel lock in db2_mirror — messages are mirrored strictly
   one at a time, in the order they arrived in DB. DB2 order now always
   equals DB order: cover post first, then videos/srt/notes.

TESTING (sandbox mocks, no live Telegram): py_compile PASS; behavior tests
cover echo image as DOCUMENT skipped, echo photo with emoji differences
skipped, video with the SAME caption kept, srt/text-notes/unrelated images
kept, and DB2 ordering preserved even when the cover copy is artificially
slow (cover lands first). Code presence verified in shipped files.
Not tested against live Telegram (no session in sandbox) — watch Render logs
for "skipped N bot echo image(s)" on the next scraped posts.
================================================================================
