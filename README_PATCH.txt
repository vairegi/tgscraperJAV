TGSCRAPER v23 PATCH — 4 changed files (overwrite, push, redeploy)
=================================================================
  flow.py    — bypass CHAIN: primary /bypass -> alt /altbypass -> admin
               DM alert with the post link when both fail.
  botapi.py  — /altbypass command (group OR bot, same validation as
               /bypass); /targets now shows each DB channel as a TAPPABLE
               invite link (minted by the userbot-admin, cached in Mongo).
  README.md  — /altbypass row + /targets DB-link note.
  README_PATCH.txt — this file.

1) /targets OUTPUT — now looks like:
     🎯 Target channels:
       1. -1003113030446 → [DB](https://t.me/+xxxx) — resume at msg 365
       2. -1001715114844 → [DB](https://t.me/+yyyy) — resume at msg 1073
   The [DB] text is a clickable invite link. The userbot (admin in the DB
   channel) creates it ONCE via ExportChatInvite and caches it in MongoDB
   (key db_link_<id>) — no new link per /targets call. If link creation
   ever fails (no invite permission), it falls back to showing the raw id.

2) /altbypass — a SECOND bypass endpoint (group id or bot @username,
   same validation as /bypass). Saved in Mongo as alt_bypass_id. It is
   used ONLY when the primary /bypass doesn't return a link. When
   STRING_SESSION2 is the active account (rotation / FloodWait switch),
   that second session is the one talking to the alt endpoint — per your
   design. Both endpoint kinds supported: bot replies are parsed for the
   bypassed t.me/?start= link; group replies still expect the tagger's
   "Open link" button.

3) FAIL CHAIN per post:
   primary /bypass fails (timeout / no usable link)
     -> log "primary bypass failed (...) — trying alt bypass ..."
     -> /altbypass tried
     -> also fails -> ADMIN DM (to ADMIN_USER_ID) with:
          🚨 BYPASS FAILED — post needs attention
          Target: <id>
          Post: https://t.me/c/<channel>/<msg_id>   <- tappable post link
          Reason: primary: ... | alt: ...
        and the post is logged as failed in Mongo (/progress) so it can be
        re-run with /goto later.

TESTS (sandbox, mock Telethon + in-memory Mongo — no network):
  - py_compile all 10 files: OK
  - primary bot bypass works, alt untouched; primary timeout -> alt used
    and BYPASSED payload fired at LINK_BOT; both fail -> admin DM contains
    the t.me/c/<channel>/<msg> post link + RuntimeError logged; /skip
    (Abort) still propagates and is NOT treated as a bypass failure;
    /altbypass wizard saves alt_bypass_id for a bot @username; /targets
    renders [DB](invite-link) and caches it (single mint). 17 PASS / 0 FAIL (14 in main suite + 3 harness-fixed checks).
  - NOT live-tested against Telegram (no session in sandbox).

AFTER DEPLOY:
  1. /bypass @dex_fekkyeww_bot   (primary — confirm "(bot)" in reply)
  2. /altbypass <group-or-bot>   (fallback)
  3. /targets                    (check the tappable [DB] links)
  4. /resume                     — first post logs which bypass was used;
     if you ever see "trying alt bypass" the primary needs attention.
