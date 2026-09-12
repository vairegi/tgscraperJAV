TGSCRAPER v14 PATCH — 4 changed files (overwrite, push, redeploy)
=================================================================
  1. /reset <n> FIXED for real. Root cause: resetting deleted the progress
     doc, but the IN-FLIGHT scan pass kept walking with the old resume
     point and re-saved progress at every message — clobbering your reset
     (that's why target 2 kept resuming where it left off). Now /reset and
     /goto bump a 'reset generation' flag; the running pass aborts
     immediately and the next pass starts from the FRESH progress.
     Verified: mid-run reset -> processed msg 51 -> reset fires ->
     next pass starts at msg 1. Files: bot.py, botapi.py, flow.py.

  2. ANY video format forwarded. Root cause: an .mkv sent as a plain
     document has NO m.video attribute in Telethon, so it was silently
     skipped. New detection: m.video OR document with video/* mime OR
     filename ending .mp4/.mkv/.avi/.mov/.webm/.m4v/.ts/.flv/.wmv/.mpg/
     .mpeg/.3gp. The vids/srts split uses the same detection, so no
     duplicates either. Files: scraper.py, flow.py.

  3. Mongo URI verified live from sandbox: ping + full CRUD on every
     collection = 9/9 PASS. Your new URI is good. Test data cleaned up.

AFTER DEPLOY: /reset 2 then /start — the Render log should show
'scanning target ... from message id 0' for target 2 and .mkv files will
now land in its DB channel like everything else.
