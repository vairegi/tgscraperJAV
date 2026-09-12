TGSCRAPER v13.1 PATCH — 1 changed file (overwrite, push, redeploy)
==================================================================
  botapi.py — FIX: "videos went to a BOT instead of my DB channel".
  Root cause: -1008930316746 is the id of @scrapjavbot (a BOT), not your
  DB channel — ids like that cannot be posted to. All id-entry points now
  VALIDATE the entity type and REJECT user/bot ids with clear guidance:
    /adddb, /setdb, and the /target wizard's DB step require a CHANNEL
    /bypass requires a GROUP/CHANNEL
  BONUS: everywhere that takes an id now also accepts a MESSAGE LINK:
    /adddb https://t.me/c/5556667777/3     (copied from any message in the chat)
  — impossible to paste the wrong kind of id. Also works for /setdb and
  the /target wizard.
  /reset <n> verified per-target: /reset 2 resets ONLY the 2nd target.

HOW TO GET THE RIGHT DB ID (easiest, no tools):
  open your DB channel -> tap any message -> Copy Link
  -> /setdb 1 https://t.me/c/xxxxx/yy   (or paste during /target wizard)
  If your DB channel is PUBLIC, its @username also works.
