TGSCRAPER v24.1 PATCH — 1 changed file (overwrite, push, redeploy)
=================================================================
  botapi.py — /deletetext fix: empty-result handling + honest errors.

SYMPTOM (your screenshot): /replace on channel -1004369767119 reported
"edited 1" but /deletetext on the SAME message reported
"cleaned 0, 1 failed (no edit rights?)" — despite the userbot clearly
having edit rights (replace proved it).

ROOT CAUSE: not permissions at all. Your target text 'Mmmmmm' is the
ENTIRE content of that message, so deleting it leaves an EMPTY text
message — which Telegram API rejects with MESSAGE_EMPTY. My code counted
that rejection as a failure and guessed "(no edit rights?)" — a wrong
label for the wrong reason.

FIX:
  - When an edit would leave NOTHING behind: media posts get their caption
    cleared (legal), and TEXT-ONLY posts are DELETED entirely via the
    userbot — the only sensible outcome for /deletetext there.
  - The summary now reports the REAL error string (e.g.
    "1 failed (last error: MESSAGE_ID_INVALID)") instead of guessing
    permissions, so the next failure diagnoses itself.

TESTS (sandbox, mocks — no network):
  - py_compile all 10 files: OK
  - full-match text-only message DELETED; media post caption cleared
    (not deleted); partial match edited normally; summary "cleaned 3";
    edit failure surfaces the real error and never says "no edit
    rights?"; /replace regression intact. 9 PASS / 0 FAIL.
  - NOT live-tested (no session in sandbox).

AFTER DEPLOY: re-run your command —
  /deletetext -1004369767119 Mmmmmm
The message should now be deleted and the summary should read
"cleaned 1" (or show the actual Telegram error if something else blocks it).
