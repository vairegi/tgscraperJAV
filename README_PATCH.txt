================================================================================
README_PATCH — v32: skip ALL bot images-with-caption
================================================================================

DRAG onto the repo root: flow.py, README_PATCH.txt
(everything else unchanged from v31.)

CHANGE (flow.py): the media-bot collection filter is now a simple rule —
ANY image (photo OR image-mime document, which is how spoiler cover images
arrive) that carries a caption is skipped and never forwarded to the DB
channel. The cover post itself is unaffected: send_cover() sends the cover
from the TARGET channel directly, and this filter only runs on the media
bot's collected messages. Videos and .srt files are excluded before the
filter and are NEVER skipped (a video with a caption is always kept);
text-only notes (deletion warnings etc.) and caption-less images are still
mirrored. Skips are logged: "skipped N bot image(s) with caption".

This replaces the v31 word-overlap echo matching, which only caught echoes
whose caption matched the cover caption.

TESTING (sandbox mocks, no live Telegram): py_compile PASS; behavior tests
cover: document-image with caption skipped, photo with caption skipped,
caption-less photo kept, video with caption kept, .srt kept, text note kept,
old overlap logic removed, and send_cover() still receives the original
target-channel message. Not tested against live Telegram (no session in
sandbox) — watch Render logs for the skip line on the next scraped posts.
================================================================================
