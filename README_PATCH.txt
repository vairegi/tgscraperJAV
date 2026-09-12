TGSCRAPER v16 PATCH — 1 changed file (overwrite, push, redeploy)
=================================================================
  forwarder.py — fixes BOTH critical problems at once:

  1) WRONG FORMAT: v15 re-uploaded videos from an in-memory buffer, which
     drops video attributes — they arrived as generic documents (just a
     download arrow, no duration/thumbnail/play). Now every file is
     re-uploaded WITH its original document attributes (duration,
     width/height, streaming support, filename) + its thumbnail, so videos
     land in the DB channel in EXACTLY the format MEDIA_BOT sent (playable
     inline, same name, same preview). Same for the cover post: same image,
     spoiler kept, caption + buttons preserved, no forward tag.

  2) OUT OF MEMORY (512MB): v15 buffered each video fully in RAM via
     BytesIO — a 700MB video killed the instance. Now media streams to a
     TEMP FILE ON DISK (constant small memory regardless of file size) and
     is deleted right after sending. RAM usage stays flat no matter how
     big the video.

  Root cause of BOTH was the same v15 choice; this replaces it. Flow
  (flow.py) unchanged — already collects all media types and sends cover
  first. Account-rotation note: disk downloads use the fetching account's
  session (which has the access hashes), so acc2 works too.

AFTER DEPLOY: re-run any post that arrived as a plain document:
  /goto <that post's link>  then  /start
