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
| `/bypass <id-or-@bot>` | set bypass endpoint — group id OR bot @username (e.g. `@dex_fekkyeww_bot`) |
| `/altbypass <id-or-@bot>` | fallback bypass — tried only if `/bypass` returns no link; both failing DMs the admin the post link |

`/targets` shows each target's DB channel as a tappable invite link (minted by the userbot, which is admin there, and cached).
| `/linkbutton [text]` | list LINK_BOT button labels, or add one — matched immediately, **no restart** |

Each target's LINK_BOT is discovered per-post from the Download button's own `t.me/<bot>?start=...` URL, and MEDIA_BOT is discovered from LINK_BOT's final reply — so different target channels can use completely different bot pairs with no config.
| `/removelinkbutton <n>` | remove custom label #n (see `/linkbutton` for numbers) |
| `/adddb <id>` | set database channel |
| `/lastpost [n]` | show the newest real post in target n (default 1) |
| `/start` | start scraping |
| `/pause [n]` | pause — bare = ALL targets; `/pause 2` = only target 2, others keep scraping. Persisted in MongoDB (crash-safe) |
| `/resume [n]` | bare = resume everything; `/resume 2` = resume only target 2 from its saved message id |
| `/status` `/current` | live stage & current post |
| `/progress` | posts done, **last scraped post**, resume point, media sent, failures + reasons |
| `/ping` | check the bot is alive (works in control bot AND userbot) |
| `/skip` `/stop` | skip current post / stop |
| `/replace <ch> "old" "new"` | userbot edits every post containing `old` in that channel, replacing all occurrences |
| `/deletetext <ch> "text"` | userbot removes `text` from every matching post in that channel |

Bulk edits are **Telegram-safe paced**: after scanning, a background worker edits ONE message every `BULK_EDIT_DELAY` seconds (default 2.5s — tune via Render env), sleeps through FloodWait errors in place and retries the same message (up to `BULK_MAX_FLOOD`, default 900s), and posts live progress into the status message every 10 edits. The control bot stays responsive during long runs. 500-message run at default pacing ≈ 21 min.

`/help` and the tappable menu are generated from the same command list, so every command above appears in both. Telegram caches the "/" menu — if it looks stale after a redeploy, close/reopen the bot chat.

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
spoiler flag, caption and buttons all preserved. Every send is VERIFIED:
Telegram can silently accept a send whose file reference is dead (message
created with no media — nothing appears in the channel); the bot detects
this, deletes the empty message, refetches the source for a fresh
reference and retries. Everything the media bot sends is mirrored —
videos, stickers, photos AND text notes (like its deletion warnings).
Media collection waits for the actual VIDEO to arrive before its
quiet-timer can end collection — the media bot posts stickers instantly
but uploads videos slowly, and a post with ZERO videos is failed +
retried, never archived as sticker-only.

IDs: use the numeric id (e.g. `-1001234567890`) or @username.
The account must be a member of the target channel, bypass group (with
post permission), and admin (post rights) in the DB channel.

## Multi-session later
`session_manager.py` already round-robins — add more StringSessions to scale.
