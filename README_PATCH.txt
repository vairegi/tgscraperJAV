TGSCRAPER v12 PATCH — 6 changed files (overwrite, push, redeploy)
=================================================================
1. requirements.txt — telethon 1.40.0 -> 1.42.0. YOUR LOG HAD A FATAL
   CRASH: TypeNotFoundError (Constructor ID 94345242) — a known 1.40 bug
   when Telegram sends new TL objects in updates (yours came from the
   CANTARELLA BYPASS GROUP). 1.42 parses it; no more 'ERROR fatal error'
   crashes. (Render restarted and recovered via Mongo, but the crash is
   now eliminated at the source.)
2. botapi.py  — MULTI-TARGET commands:
     /target    now ADDS a channel to your list (no longer replaces)
     /targets   lists all targets with their resume points
     /deltarget removes one (send its id or its list number)
     /status shows the full targets list; /lastpost uses the first target
3. db.py      — add_target/remove_target/get_targets helpers. Progress is
   tracked PER channel, so every target resumes independently.
4. bot.py     — scrape loop works through ALL targets: each pass picks the
   channel with the oldest (least-scraped) progress, so channels are
   scraped in turn. Legacy single-target config is auto-compatible.
5. config.py  — NEW ENV: MEDIA_BOT (default @Rias_Gremory_Robot). The
   media-fetching bot is no longer hardcoded — change it in Render env
   vars anytime (e.g. MEDIA_BOT=@SomeOtherBot), no code edit needed.
   flow.py   — uses MEDIA_BOT (imports it from config).

NOTE ON MEDIA_BOT: the link from Fubuki contains the bot's username, so
the flow already opens whatever bot the link points to; MEDIA_BOT is the
fallback/identity for that step. If your new chain names a different bot
in the link, it will still work — the code follows the link's bot.

TO SWITCH TARGET CHANNEL: /deltarget (remove old) -> /target (add new)
-> /start. Each channel keeps its own progress in MongoDB.
