TGSCRAPER v17.1 PATCH — 2 changed files (overwrite, push, redeploy)
===================================================================
  flow.py   — FIX: DB channel got ONLY stickers, videos missing.
  README.md — documents the fix.

SYMPTOM (your report): MEDIA_BOT delivered cover video + sticker burst +
video files, but the DB channel received ONLY the stickers for that post.

ROOT CAUSE (found in flow.py _collect_media): the media bot posts its
decorative stickers + text INSTANTLY, then takes many seconds to upload
the actual video files (hundreds of MB). Collection stopped after a
5-second quiet gap — which fired in the pause BETWEEN the sticker burst
and the first video upload finishing. Since cover+stickers had already
been "collected", the flow archived exactly that: cover + stickers, no
videos. Not a forwarder/v17-send problem — the send stage never got the
videos at all. The 20s Fubuki timeout in your Render log was a separate
hiccup on the post before this one.

FIX:
  - The quiet-gap timer is now ARMED ONLY after at least one VIDEO has
    arrived — an early sticker burst can no longer end collection.
  - If collection ends with messages but ZERO videos, the post FAILS
    loudly ("N message(s) but NO video ... not archiving this post") and
    is logged in Mongo (visible via /progress) — instead of silently
    archiving sticker-only junk. It will be retried next scan (failed
    posts don't advance last-post).
  - Added one log line per post: "collected N msg(s): X video + Y srt +
    Z other" so every post's delivery is verifiable in Render logs.
  - Tunables unchanged and env-overridable: collection window 90s,
    quiet gap 5s after the last video.

  forwarder.py UNCHANGED (v17 reference-send is not the problem).

TESTS (sandbox, mock Telethon — no network):
  - py_compile all files: OK
  - _collect_media behavior tests: stickers-first+late-video collects
    videos correctly (regression for this bug); sticker-only collection
    raises; quiet-gap after videos still stops collection. 8 PASS / 0 FAIL.
  - NOT live-tested (no session in sandbox).

AFTER DEPLOY: the sticker-only DB entry needs one re-run:
  /goto <that post's link>  then  /start
