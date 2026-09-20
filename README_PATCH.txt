================================================================================
README_PATCH — v29: DB2 clean mirror (bot-powered), /setdb2, /avoidtext,
2-arg /replace for DB2
================================================================================

DRAG onto the repo root: botapi.py, db.py, README.md  (bot.py / mtprotomgr.py /
config.py unchanged from v28.)

THE PROBLEM: DB posts come from the USERBOT, so editing their captions means
userbot edits — slow, flood-prone, one-by-one.

THE FIX — a second "DB2" channel per target, powered by the BOT:
- /target wizard now asks for the DB channel AND THEN an optional DB2 channel
  (send `skip` to leave it off). Existing targets: /setdb2 <target #> <id|off>
  — NO need to remove/re-add targets (and progress is never lost either way;
  see below).
- When the userbot posts anything into a target's DB channel, the BOT
  automatically re-posts it into DB2 as a FRESH post (no forward tag) with a
  CLEANED caption: every /avoidtext string removed, plus ALL links
  (t.me/…, http(s)://…) and @mentions auto-stripped, embedded-link formatting
  dropped (captions go out as plain text). Media, spoiler flag and buttons
  are preserved. REQUIREMENT: the BOT must be admin in BOTH the DB channel
  (to see its posts) and DB2 (to post).
- /avoidtext "text" — manage the strip list per target:
      /avoidtext               list avoid-strings for every target
      /avoidtext 2             list them for target 2
      /avoidtext 2 "join us"   strip "join us" from target 2's DB2 captions
      /removeavoid 2 1         remove string #1
- /replace "old" "new" — the 2-argument form now edits every DB2 channel
  WITH THE BOT (DB2 posts are the bot's own, so editing is allowed — no
  userbot, no flood hassle). The 3-arg form /replace <ch> "old" "new" is
  unchanged (userbot edits any channel).
- /targets shows each target's DB2 channel inline:  Target → DB → DB2.

YOUR QUESTION — adding DB2 to the existing 6 targets: just run
/setdb2 1 <id> … /setdb2 6 <id>. Removing and re-adding is NOT needed. If you
DID remove a target and add it again later, its progress is KEPT
(/deltarget never deletes progress) — it resumes from the last scraped
message, not from the start. Both paths are safe.

TESTING (sandbox mocks, no live Telegram): py_compile PASS on all files;
behavior tests cover target migration with db2_id/avoid, set/add/remove
avoid strings, wizard asking for DB2 (skip path), /setdb2 set/off by target
number, /avoidtext add+list, /removeavoid, the DB→DB2 mirror copying media
with @mentions/links/avoid-strings stripped and spoiler preserved, non-DB
chats ignored, text-only mirroring, and 2-arg /replace editing DB2 via the
bot. One real-world check after deploy: make sure the BOT is admin in the DB
channels (or it won't receive their posts to mirror).
================================================================================
