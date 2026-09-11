TGSCRAPER v4 PATCH — 3 changed files (overwrite, push, redeploy)
================================================================
  1. scraper.py — THE POST-DETECTION FIX. Spoiler images arrive as DOCUMENT
     media, not photos; the old photo-only filter skipped every post in your
     channel (msgs 11-346 all 'skip ... not a post'). Now media = photo OR
     document OR video OR web-preview, + caption + inline buttons + Download
     button. Added why_not_post() so logs show WHY each skip happens.
  2. bot.py     — skip logs now show the reason: 'skip msg 12 (no media)' etc.
  3. botapi.py  — /goto now accepts message LINKS:
                    /goto https://t.me/c/2514892126/11   (starts at msg 11)
                  Refuses links that belong to a different channel than /target.

YOUR FORMAT (img+spoiler + 2 buttons: 'Download' + 'How to Access Link') now
matches: Download found via case-insensitive partial match; the other button
is ignored as intended.

THEN RUN:
  /reset  ->  /goto https://t.me/c/2514892126/11  ->  /start
  (or just /reset -> /start to begin from message 1)
Watch Render log: you should see 'POST FOUND: msg N' lines and the
Download-click flow begin.
