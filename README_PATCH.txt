TGSCRAPER v19 PATCH — 5 changed files (overwrite, push, redeploy)
=================================================================
  botapi.py — NEW /linkbutton & /removelinkbutton commands (menu + /help).
  flow.py   — LINK_BOT link-button matching uses defaults + your custom
              labels; the failure message now names the exact labels tried.
  db.py     — link_buttons list in Mongo + add/remove helpers (de-duped).
  scraper.py— find_button accepts a LIST of aliases (first match wins).
  README.md — command table updated.

WHY: LINK_BOT renamed its "Short link" button -> posts failed with
"Fubuki sent neither a Short link button nor a link". Instead of shipping
a new zip per rename, labels are now OWNER-MANAGED AT RUNTIME:

  /linkbutton "Get Link 🔗"   add a label (quotes optional) — active on the
                              very next post, NO restart/redeploy
  /linkbutton                 list: built-in default + your numbered labels
  /removelinkbutton 1         remove label #1 (numbers from /linkbutton)

Matching is unchanged otherwise: case-insensitive partial match after
Unicode-folding (fancy fonts like 𝗚𝗲𝘁 𝗟𝗶𝗻𝗸 match plain "get link"),
de-duped so variants don't pile up. Labels live in MongoDB — they survive
Render restarts. The built-in "short link" label always stays active.

The 60s bypass timeout in your last log was NOT this bug — the tagger was
just slow (bypass button is still "Open link"). If it repeats, raise
WAIT_BYPASS_REPLY (e.g. 120) in Render env.

TESTS (sandbox, mock Telethon + in-memory Mongo, no network):
  - py_compile all 10 files: OK
  - db: add/de-dupe (fancy-font twin rejected)/remove/out-of-range: PASS
  - scraper.find_button: list aliases match any label, first match wins,
    no-match returns None, single-string behavior unchanged: PASS
  - botapi: /linkbutton add (quoted + unquoted), duplicate rejected, list
    empty + populated, /removelinkbutton by number + out-of-range,
    /help + menu contain both commands: PASS
  - 25 PASS / 0 FAIL. NOT live-tested against Telegram (no session).

AFTER DEPLOY: /linkbutton <the new button's exact text> — done. Verify
with /linkbutton, then watch one post: it should pass "getting short link".
