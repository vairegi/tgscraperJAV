TGSCRAPER v9 PATCH — 1 changed file (overwrite, push, redeploy)
================================================================
  flow.py — DUPLICATE VIDEOS FIXED. In Telethon, a video message has BOTH
  .video AND .document set. The split was:
      vids = [m for m in media if m.video]
      srts = [m for m in media if m.document]     <- videos matched this too
  so vids + srts contained every video TWICE -> vid1, vid1, vid2, vid2 in
  your DB channel (exactly your screenshot). Now:
      srts = [m for m in media if m.document and not m.video]
  Each video is sent exactly once; the .srt still ships once.

AFTER DEPLOY: continues from saved progress; nothing else to redo.
Already-duplicated posts in the DB channel: delete those copies manually,
or /goto that message id + /start to redo a specific post cleanly.
