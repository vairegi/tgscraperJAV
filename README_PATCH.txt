TGSCRAPER v10 PATCH — 3 changed files (overwrite, push, redeploy)
=================================================================
  config.py — CONSERVATIVE PACING (ban-safe):
      STEP_DELAY 5s (was 2)  — between each workflow step
      POST_DELAY 45s (was 10) — between posts; one post fully done first
      FLOOD_MAX_WAIT 1800     — FloodWaits longer than this are capped
      FLOOD_PARK 1800         — park duration when capped
  flow.py   — extra STEP_DELAY before DB delivery (spaces out the uploads).
  bot.py    — FloodWait handling: short waits sleep in-process as before;
              waits OVER 30 min now PAUSE the scraper and park 30 min, then
              auto-resume from the same post — instead of one giant sleep.
              Progress is saved before every post, so nothing is lost.

WHY THE 1851s FLOOD HAPPENED: ~15 rapid posts x ~8 Telegram actions each
(2x Fubuki /start, bypass send, Rias /start, cover copy, 1-2 video
re-uploads, srt) tripped Telegram's rate limiter. The new pacing spreads
actions out; if a flood still hits, it parks cleanly instead of stalling.

RESUME FROM POST 16:
  /goto https://t.me/c/2514892126/16   (or just: /goto 16)  then  /start
Note: /goto uses the MESSAGE ID. 'Post 15' in your terms = the 15th real
post, which may not be message id 15 — use /lastpost to see real ids, or
just /goto 16 if you know msg 16 is the one.
Optional: to slow it further, set Render env POST_DELAY=90 (no redeploy of
code needed — just restart after adding the env var).
