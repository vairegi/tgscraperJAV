================================================================================
README_PATCH — v27: /add adaptive rights + categorized /help
================================================================================

TWO FIXES (drag these files onto the repo root, replacing existing ones):

1) /add — "Either your admin rights do not allow you to do this … rights only
   apply to channels and vice versa (caused by EditAdminRequest)".

   WHY IT HAPPENED: two Telegram rules collide.
   - An admin can only grant rights IT ALREADY HAS — you can't pass a right
     the userbot doesn't hold.
   - manage_call + other are GROUP-only rights. The target is a broadcast
     CHANNEL, so setting them makes Telegram reject the whole request with
     the exact "only apply to channels and vice versa" error.

   FIX (mtprotomgr.py): /add is now ADAPTIVE.
   - It first reads the userbot's OWN rights in that channel
     (GetParticipantRequest) and builds the grant from the intersection —
     it never asks for a right the userbot lacks.
   - Group-only rights (manage_call, other) are dropped automatically in
     broadcast channels and only granted in megagroups.
   - If Telegram STILL rejects the full set, it retries once with a reduced
     channel-safe set (post/edit/delete/invite) and reports which rights were
     actually granted per bot, e.g.:
       ✅ @loverxnbot — added as admin [post, edit, delete, ban, invite, pin, add_admins]
   So the bots are added with AS MANY permissions as the userbot can give.
   (If a right is missing it's because the USERBOT doesn't have it — give the
   userbot that right first and re-run /add.)

2) /help — was one long flat list. Now grouped under headings:
   ℹ️ INFO · 🎯 SETUP · 🔘 LINK-BOT BUTTONS · ▶️ SCRAPING · 📊 MONITOR ·
   🧭 PROGRESS CONTROL · ✏️ BULK TEXT EDIT · 🧹 MASS DELETE · 📨 FORWARD/COPY
   · 🤖 ADD BOTS — so any command is easy to find.

CHANGED FILES: mtprotomgr.py, botapi.py  (config.py / README.md unchanged from v26)

TESTING (sandbox, mocks — no live Telegram): py_compile PASS; behavior tests
9 PASS / 0 FAIL (adaptive rights grant, no group-only rights in a channel,
reduced-rights retry on rejection, /help has all headings, every command
listed exactly once). Not tested against live Telegram (no session in sandbox).
================================================================================
