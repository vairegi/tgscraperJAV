TGSCRAPER v11.1 — CLEANER MULTI-ACCOUNT ENV VARS (1 file)
==========================================================
Just replaces config.py from v11 — everything else stays.

Instead of one comma-separated STRING_SESSIONS, use SEPARATE env vars:

  STRING_SESSION   = <session for account 1>
  STRING_SESSION2  = <session for account 2>
  STRING_SESSION3  = <session for account 3>   (optional, up to 20)

That's it — the bot picks them up automatically in numeric order.
Blank / missing slots are skipped; gaps are fine (SESSION + SESSION3 works).

BACKWARD COMPATIBLE:
- Only STRING_SESSION set  -> 1 account (same as before v11)
- STRING_SESSIONS (comma)  -> still works as a fallback
- Numbered slots win over STRING_SESSIONS if both are set

RENDER SETUP (2 accounts, your case):
  1. Env Vars page -> Add:
       Key:   STRING_SESSION2
       Value: <paste account-2 StringSession here>
  2. Save -> Redeploy.
  3. Log should say: "logged in as ... - 2 account(s) loaded, rotating every 20 posts"

Both accounts must be MEMBER of target channel + bypass group (with message
permission) and ADMIN in the DB channel (post rights).
