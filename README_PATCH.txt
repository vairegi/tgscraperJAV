TGSCRAPER v13 PATCH — 3 changed files (overwrite, push, redeploy)
=================================================================
  db.py      — targets are now {id, db_id} PAIRS: every target channel has
               its OWN database channel. Legacy data auto-migrates (old
               single target + /adddb fallback keep working). Progress is
               per-channel and SURVIVES /deltarget (re-adding resumes).
  botapi.py  — FIX: /adddb <id> (and all wizards) accept the id INLINE —
               your '/adddb -1003998574377' was being swallowed before.
               NEW two-step /target: asks channel id, then THAT channel's
               DB channel. NEW /setdb (send: 2 -100999888777) to change a
               target's DB later. /targets shows the full mapping with
               resume points. /reset and /goto are PER-TARGET now
               (/reset 2, /goto 2 120, or /goto <message link> — the link
               auto-picks the right channel). NEW /help lists all 19
               commands. Menu updated.
  bot.py     — loop routes each post to its target's OWN db channel
               (per-target db_id wins over the /adddb fallback) and shows
               per-target lines in /progress. Caught-up channels re-scan
               for new posts every 30 seconds.

AFTER DEPLOY (your plan):
  /deltarget 1            -> remove old channel (progress kept)
  /target                 -> channel id -> its DB channel id   (repeat per channel)
  /targets                -> verify the mapping
  /goto 2 50 or /goto <message link>   -> per-target start point
  /start

ANSWERS TO YOUR QUESTIONS:
- Re-adding a removed channel RESUMES where it left off (not post 1);
  if caught up it waits — new posts are detected within ~30 seconds.
- /goto is safe with multiple targets: bare '/goto 3' is refused with a
  picker when you have 2+ targets; use '/goto <number> <msg_id>' or a
  message link (auto-detects the channel from the link).
