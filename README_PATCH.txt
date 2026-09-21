================================================================================
README_PATCH — v34: /checkdm on|off — @richmining invite -> auto admin pipeline
================================================================================

DRAG onto the repo root: checkdm.py (NEW), bot.py, botapi.py, README_PATCH.txt
(everything else unchanged from v33.)

NEW COMMAND: /checkdm on | /checkdm off   (bare = show current state)

When ON, the USERBOT watches its DM with @richmining. Each channel invite
link he sends — public (t.me/name) or private (t.me/+hash, t.me/joinchat/…) —
triggers this pipeline:
  1. JOIN the channel,
  2. WAIT until @richmining promotes the userbot to admin (polled every ~5s,
     up to 15 min),
  3. ADD @lifesimplerbot as admin with EXACTLY the rights the userbot holds
     there — never more (Telegram forbids it). Group-only rights are dropped
     automatically in broadcast channels; one reduced-rights retry if the
     full set is rejected,
  4. LEAVE the channel,
  5. REPLY to the link message: DONE ✅ + the invite link,
then it's ready for the next link.

SAFETY: joins are paced, FloodWait slept through in place, up to 5 channels
processed concurrently, each link handled once. If no admin rights arrive
within 15 min, the userbot leaves and warns @richmining. The on/off flag is
MongoDB-backed (survives restarts). Registered on EVERY userbot session, so
account rotation never breaks it.

TESTING (sandbox mocks, no live Telegram): py_compile PASS on all files;
behavior tests cover invite parsing (public / +hash / joinchat / no-link),
the full job flow (join -> poll until promoted -> EditAdminRequest with
mirrored rights -> LeaveChannelRequest -> DONE reply with the link), the
timeout path (leaves + warns, no promotion), flag-OFF / wrong-sender /
non-DM / no-link all ignored, and the /checkdm on|off|status command.
NOTE: not tested against live Telegram — the first real run depends on
@richmining actually promoting the userbot (only then can it add
@lifesimplerbot), so watch the first one in Render logs.
================================================================================
