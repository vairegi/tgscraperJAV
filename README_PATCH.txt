================================================================================
README_PATCH — v28: titled/linked /targets + /progress, /addadmin /removeadmin
================================================================================

DRAG onto the repo root: botapi.py, bot.py, db.py, README.md
(mtprotomgr.py / config.py unchanged from v27.)

1) /targets + /progress now show CHANNEL TITLES as tappable links instead of
   bare ids, e.g.:

     🎯 Target channels:
       1. [HAnime Alliance] → [DB Vault] — resume at msg 372
       2. [I-ANIME] → (fallback /adddb) — resume at msg 1075
       3. [NSFW UNIVERSE] ⏸ → [DB Vault] — resume at msg 358

   - PRIVATE targets have no invite link, so the title links to the channel's
     LAST SCRAPED post (t.me/c/<internal>/<msg>) — opens fine for any member,
     no invite needed.
   - DB channels use their cached invite link (already minted by the userbot);
     if unavailable, the plain title is shown.
   - Titles come from the userbot (it's a member) and are cached in memory;
     unknown/inaccessible chats fall back to the raw id. Paused targets get a
     ⏸ flag in /targets too.

2) /addadmin — delegate full bot control (owner only):
     /addadmin <user id>   give that Telegram user FULL control-bot access
                           (every command, exactly like the owner)
     /addadmin             list owner + current admins
     /removeadmin <user id>  revoke an admin
   Ids are NUMERIC Telegram user ids (get them from @userinfobot). The admin
   list lives in MongoDB, so it survives Render restarts/redeploys. Only the
   owner (ADMIN_USER_ID env) can add/remove admins — added admins cannot add
   more admins.

TESTING (sandbox mocks, no live Telegram): py_compile PASS on all 10 files;
behavior tests 28 PASS / 0 FAIL (title+link rendering for targets/DB,
fallback to plain id, resume numbers + paused flag kept, /progress titled,
admin add/remove/list, non-owner blocked from managing admins, /help ADMINS
section + every command still listed, menu entries present).
================================================================================
