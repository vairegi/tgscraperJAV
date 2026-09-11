TGSCRAPER v8 PATCH — 4 changed files (overwrite, push, redeploy)
================================================================
  forwarder.py — DB DELIVERY FIXED. v7 used send_message(..., spoiler=...) but
     Telethon's send_message has NO 'spoiler' kwarg -> TypeError killed every
     post at the cover step, so videos never reached the DB channel (your log:
     posts 15-20 'unexpected keyword argument spoiler'). Now send_file
     everywhere (it supports spoiler). Still copy-mode: no 'Forwarded from'
     tag, buttons + spoiler preserved, albums grouped.
  flow.py      — DOUBLE-LINK FIXED. 'Open link' in the bypass group deep-links
     back to Fubuki; _follow_button sends /start <payload> once, but leftover
     code sent the SAME payload again -> Fubuki replied twice (your screenshot:
     two 'Here is your link' messages). Duplicate removed: exactly ONE /start
     per hop. STEP_DELAY (2s) added between workflow steps.
  config.py    — new env knobs: STEP_DELAY (2s), POST_DELAY (10s).
  bot.py       — 10s pause between posts: one post fully processed at a time,
     ban-safe pacing.

AFTER DEPLOY: continues from saved progress. Posts 15-20 failed AFTER Rias
delivered (cover step) so they won't auto-retry — to redo them:
/goto 15 then /start.
