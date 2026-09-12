# Telegram MTProto Userbot — Post/File Scraper

Scrapes a target channel post-by-post: clicks **Download** → @Fubuki_xRobot
**Short link** → sends link to a **bypass group** → clicks the tagged **Open link**
→ Fubuki final link → @Rias_Gremory_Robot → forwards **cover post + video(s) + .srt**
to your database channel. Progress/config/stats live in MongoDB.

## Deploy (Render free web service)
1. Push this repo to GitHub.
2. Render → New → Web Service → connect repo (or use render.yaml blueprint).
3. Env vars: `API_ID`, `API_HASH`, `STRING_SESSION`, `MONGO_URI`, plus
   `BOT_TOKEN` (from @BotFather) and `ADMIN_USER_ID` (your numeric id from
   @userinfobot) to enable the **control bot with a tappable / command menu**.
4. Free instances sleep — ping `https://<app>.onrender.com/health` every ~10 min
   with an uptime monitor (UptimeRobot etc.).

## Two ways to control it

**A) Control bot (recommended):** open a chat with YOUR bot (the BOT_TOKEN one).
Tap `/` — a menu lists every command. Tap `/target`, `/bypass` or `/adddb`
with NO argument: the bot asks for the id, you send it, it validates and
saves it. Add all three one by one, then tap `/start`.

**B) Userbot commands:** type these in any chat (Saved Messages recommended):

| Command | Action |
|---|---|
| `/target <id>` | set target channel |
| `/bypass <id>` | set bypass group |
| `/adddb <id>` | set database channel |
| `/lastpost` | show the newest real post in the target channel |
| `/start` | start scraping |
| `/pause` | pause — progress persisted in MongoDB (crash-safe) |
| `/resume` | resume exactly from the last saved message id |
| `/status` `/current` | live stage & current post |
| `/progress` | posts done, **last scraped post**, resume point, media sent, failures + reasons |
| `/ping` | check the bot is alive (works in control bot AND userbot) |
| `/skip` `/stop` | skip current post / stop |

## Flood-wait protection (v3)
If Telegram returns FloodWaitError at login or mid-scrape, the process now
SLEEPS in place for the required seconds instead of crashing — so Render never
enters a crash-restart loop and Telegram's flood timer is never refreshed.

## Crash resilience
Progress (last processed message id) is written to MongoDB after EVERY post.
If Render crashes or restarts, the bot resumes from that exact point —
nothing is scraped twice.

## Copy-mode delivery (v17)
The cover post and all media arrive in the DB channel as FRESH posts with NO
"Forwarded from" tag — the userbot re-sends each message's media by its
Telegram file reference (send_file with msg.media), so Telegram copies the
file server-to-server. Nothing is downloaded to disk or RAM (no temp files,
stays flat on the 512MB free tier even for 700MB+ videos), and every file
keeps its original format: playable video with thumbnail/duration/filename,
spoiler flag, caption and buttons all preserved.

IDs: use the numeric id (e.g. `-1001234567890`) or @username.
The account must be a member of the target channel, bypass group (with
post permission), and admin (post rights) in the DB channel.

## Multi-session later
`session_manager.py` already round-robins — add more StringSessions to scale.
