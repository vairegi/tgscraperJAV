================================================================================
README_PATCH — v33: no duplicate on resume, explicit /pause all / /resume all
================================================================================

DRAG onto the repo root: bot.py, botapi.py, README_PATCH.txt
(everything else unchanged from v32.)

1) NO DUPLICATE ON RESUME (bot.py)
   Bug: pause a target at post 149, resume -> 149 was scraped AGAIN
   (duplicate in the DB channel). Cause: v30 saved progress as msg.id - 1,
   so a resume re-processed the completed post.
   Fix: progress is saved as msg.id again. The pause-skip that v30 was
   fixing came from a race — /pause waited for the in-flight post, but the
   next scan had read the OLD progress before the post finished. The loop
   already restarts the pass on /pause (reset_gen bump) and re-reads fresh
   progress, so saving the correct id is now safe: iter_messages(min_id=id)
   is exclusive, so the finished post is NOT re-scraped and the next post
   is NOT skipped.

2) + 3) EXPLICIT GLOBAL PAUSE/RESUME (botapi.py)
   Bare /pause and bare /resume no longer do anything (mistype-proof). They
   reply with a hint instead:
     /pause all    — pause everything        /resume all   — resume everything
     /pause 2      — pause only target 2     /resume 2     — resume target 2
   The / menu descriptions and the /help tip were updated to match.

TESTING (sandbox mocks, no live Telegram): py_compile PASS; behavior tests
11 PASS / 0 FAIL — bare /pause and /resume refused with hints, /pause all
and /resume all work, /resume all clears per-target pause flags, per-target
/pause 1 / /resume 1 unchanged and shows the resume point, non-numeric args
get usage, progress saved as msg.id (no -1 anywhere), menu text updated.
Not tested against live Telegram (no session in sandbox).
================================================================================
