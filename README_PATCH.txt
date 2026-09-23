================================================================================
README_PATCH — v39.1: resume-visibility logging + /checkram + addadmin gate fix
================================================================================

DRAG onto the repo root: bot.py, botapi.py, README.md, README_PATCH.txt
(everything else unchanged from v39.)

1) RESUME-VISIBILITY (bot.py): the parallel dispatcher now LOGS what it sees —
   a membership pre-check ('account 1 cannot READ target … every userbot must
   be a MEMBER'), a 'caught up — no new posts' line when a resumed target has
   nothing to scrape, and a per-wave 'parallel wave: posts [..] -> accounts [..]'
   line. Worker failures now include the exception type + a join-channel hint.
   Root cause of 'resumed but silent' is now visible in the Render log instead
   of guessing.

2) /checkram (botapi.py): shows the process RSS and the container's cgroup RAM
   usage/limit — watch Render's 512 MB free-tier cap without opening the
   dashboard. Added to the command menu and /help (INFO section).

3) /addadmin + /removeadmin: re-applies the _owner -> _admin gate fix if the
   repo still had the old gate (any existing admin can manage admins; no more
   silent no-reply when messaging from a second account).

TESTING: py_compile PASS on bot.py + botapi.py. /checkram reads /proc/self/
status + /sys/fs/cgroup (no new dependency — requirements.txt untouched).
================================================================================

================================================================================
README_PATCH — v39: parallel multi-userbot scraping + multi-bypass routing
================================================================================

DRAG onto the repo root: bot.py, flow.py, forwarder.py, scraper.py, db.py,
botapi.py, README.md, README_PATCH.txt (everything else unchanged from v38).

--------------------------------------------------------------------------------
1) PARALLEL MULTI-USERBOT SCRAPING + STRICT DB DELIVERY LOCKING
--------------------------------------------------------------------------------
• With 2+ StringSessions, accounts no longer rotate one-after-another — they
  scrape the CURRENT target's pending posts IN PARALLEL (acc1 -> post #1,
  acc2 -> post #2, …). One remaining post -> any one free account handles it.
• STRICT DELIVERY: forwarder.deliver_post_bundle() holds a per-DB-channel
  asyncio lock for a post's WHOLE bundle — cover post FIRST, then all its
  videos/srt/other. A second userbot's post waits in line, so posts never
  interleave in DB. DB2 inherits the order via botapi's own per-DB lock.
• PROGRESS SAFETY: progress only advances to the highest CONTIGUOUS resolved
  message id, so an out-of-order finish or a flood-parked account can never
  make the scraper skip a post. A FloodWait parks just that account (its post
  is retried next pass) — no whole-loop restart, no crash loop.
• One target at a time: when it's caught up, the next pass moves to the next
  resumed target and fans its posts across all accounts the same way.
• Single account => the ORIGINAL sequential path runs, untouched.
• /pause lets in-flight posts finish (no new wave); /skip aborts ALL
  in-flight posts (recorded as 'skipped by user').
• /progress and /status now show a "Parallel workers" section.

2) MULTI-BYPASS POOL + DOMAIN-SPECIFIC ROUTING WIZARD
--------------------------------------------------------------------------------
• /bypass now manages pool slot #1; the legacy bypass_id auto-migrates into
  the pool on first run. /altbypass stays the LAST-RESORT fallback.
• NEW COMMANDS: /addbypass · /removebypass <n> · /bypasslist ·
  /domainbypass (wizard, or /domainbypass <domain> <@bot>) · /deldomain <n>.
• ROUTING (flow.py step 4): a short link whose domain matches a /domainbypass
  rule goes ONLY to that bot (e.g. babylinks.in -> @BypassBot_A, aerolinks.*
  -> @BypassBot_B). Any other link tries every pool bot in order, then
  /altbypass.

3) BYPASS FAILURE RETRY + ADMIN ALERT
--------------------------------------------------------------------------------
• Each bypass endpoint gets 2 attempts (initial + 1 retry).
• A 2nd failure (or an invalid reply) fires an INSTANT ⚠️ Bypass Failure
  Alert via the control bot to ADMIN_USER_ID + every /addadmin admin
  (userbot-DM fallback), formatted as:
      ⚠️ **Bypass Failure Alert**
      • **Failed Link:** <short_link>
      • **Bypass Bot:** @<bypass_bot>
      • **Userbot Used:** <userbot session name>
      • **Target Channel:** <target_channel_id>
      • **Post Msg ID:** <msg_id>
• If the WHOLE chain still fails, the owner also gets the old tappable
  post-link DM, and the post is recorded in /progress and skipped — the loop
  never crashes or gets stuck.

--------------------------------------------------------------------------------
TESTING (sandbox mocks, no live Telegram): py_compile PASS on all changed
files. Behavior tests (with config/db/botapi/telethon stubbed) cover:
domain extraction + wildcard/suffix matching; the bypass chain (domain rule
uses ONLY its bot; unruled links walk every pool bot in order then alt;
2-attempt retry + instant alert; whole-chain failure -> old admin DM); the
pool migration + add/remove; the per-DB delivery bundle ordering (cover
first, then media, no interleave); the /domainbypass 2-step wizard and
/addbypass wizard accept and persist through stubbed state; and the parallel
dispatcher (2 posts on 2 accounts concurrently, contiguous-watermark
progress, flood-park leaves the post for retry).
NOTE: not run against live Telegram here — the parallel delivery ordering and
the bypass routing are the two things to watch in the first real run's logs.
================================================================================
