TGSCRAPER v24 PATCH — 3 changed files (overwrite, push, redeploy)
=================================================================
  botapi.py  — NEW /replace and /deletetext commands (userbot-powered
               bulk editing of channel posts). Added to the command menu
               (and therefore /help, which is generated from that list).
  README.md  — command table updated.
  README_PATCH.txt — this file.

NOTE on your "commands missing from /help": the menu list in this repo
DOES contain every command we added (linkbutton, removelinkbutton,
altbypass, cancel, pause/resume with numbers...). /help prints that same
list automatically. If your live bot's "/" menu looks stale, Telegram
caches command menus aggressively — close and reopen the chat with the
bot (or restart the app) after redeploy and it refreshes.

NEW COMMANDS (admin-only, run through the USERBOT so user posts can be
edited — the userbot must have edit rights in that channel):

  /replace -1001234567890 "old text" "new text"
      Scans the channel (server-side search) and edits EVERY message
      containing "old text", replacing ALL occurrences with "new text".
      Works on captions too. Quotes are required when texts contain
      spaces. Progress is reported, then a summary:
      "scanned N matching message(s), edited M" (+ failure count if any).

  /deletetext -1001234567890 "text to delete"
      Same scan; removes the text from every message and tidies leftover
      double spaces / blank lines.

Both are safe by design: only messages containing the exact target text
are touched, edits go through the userbot (never the control bot, which
can't edit user posts), and failures are counted and reported, not hidden.

TESTS (sandbox, mocks — no network):
  - py_compile all 10 files: OK
  - /replace: quoted args parsed; all occurrences replaced; non-matching
    messages untouched; summary reported; bad args -> usage hint;
    inaccessible channel -> clean error message.
  - /deletetext: text removed, whitespace tidied, only matches edited,
    bad args -> usage hint.
  - Menu/help: replace + deletetext registered, all earlier commands
    (linkbutton, removelinkbutton, altbypass, cancel, pause, resume,
    targets) still present.
  - 14 PASS / 0 FAIL. NOT live-tested against Telegram (no session in
    sandbox) — first real run, try it on ONE known post first:
    /replace <channel> <unique word> <same word> (no-op) to confirm the
    userbot's edit rights before a real change.

WARNING: edits are permanent — Telegram has no undo. Double-check the
target text before running on a big channel.
