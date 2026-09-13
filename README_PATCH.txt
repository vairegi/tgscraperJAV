TGSCRAPER v20 PATCH \u2014 4 changed files (overwrite, push, redeploy)
=================================================================
  flow.py    \u2014 LINK_BOT + MEDIA_BOT discovered per POST (not from env).
  config.py  \u2014 FUBUKI_BOT / MEDIA_BOT env vars now OPTIONAL (fallback only).
  README.md  \u2014 documents per-target bot discovery.
  README_PATCH.txt \u2014 this file.

WHY (your report): target 1 (Hanime Alliance) and target 2 (I-ANIME Ecchi
Network) use DIFFERENT LINK_BOTs. The old code hardcoded FUBUKI_BOT from
env, so /resume 2 opened target 1's LINK_BOT for target 2's post \u2014 dead
end. Now each Download button on each post is followed to WHICHEVER bot
its own URL points at.

HOW IT WORKS NOW (per post, per target):
  1. Read the Download button's URL BEFORE clicking:
     https://t.me/<LINK_BOT>?start=<payload>  \u2192 LINK_BOT for this post
     (falls back to env FUBUKI_BOT only if the URL isn't a t.me deep link)
  2. Click it \u2014 send /start <payload> to THAT LINK_BOT \u2014 wait its reply
     on THAT chat.
  3. Grab the short link, post to bypass group, click Open link.
     If Open link points to a DIFFERENT bot (rare setups do this), the
     code switches to that bot for step 6 too.
  4. LINK_BOT's final reply contains https://t.me/<MEDIA_BOT>?start=...
     \u2192 MEDIA_BOT for this post is whatever LINK_BOT names.
  5. Videos forwarded to the DB channel exactly like before.

Every stage log now names the actual bot in use, e.g.:
  post 165: LINK_BOT=@FubukiRobot (from Download button)
  post 165: MEDIA_BOT=@Rias_Gremory_Robot (from @FubukiRobot final link)
So the next time something breaks the log tells you WHICH bot involved.

ENV VARS (Render dashboard):
  - FUBUKI_BOT / MEDIA_BOT are now OPTIONAL. You can DELETE them. They
    only kick in as a fallback if a Download button is missing a t.me
    deep-link URL (shouldn't happen with your bots).
  - Everything else unchanged.

TESTS (sandbox, mocks \u2014 no network):
  - py_compile all 10 files: OK
  - _peek_link_bot: extracts bot from t.me URL / from fancy-font button /
    None when no t.me URL / None when no Download button: PASS
  - _follow_button returns (url, button, bot_username) for tg deep links,
    (url, button, None) for plain URLs, (result, button, None) for callback
    buttons: PASS
  - process_post integration (mock client + msg): two posts with different
    LINK_BOTs (@BotA / @BotB) route to their OWN chats \u2014 no cross-target
    contamination; MEDIA_BOT picked up from each LINK_BOT's own final reply
    (@MedA / @MedB): PASS
  - config.py: FUBUKI_BOT/MEDIA_BOT default to None (not crashing) when
    env vars absent: PASS
  - 14 PASS / 0 FAIL. NOT live-tested against Telegram (no session).

AFTER DEPLOY: /resume 2 (or bare /resume). Watch Render logs \u2014 you
should see per-post "LINK_BOT=@..." / "MEDIA_BOT=@..." lines naming the
right bots for each target. FUBUKI_BOT/MEDIA_BOT env vars can be removed
from Render (or left as harmless fallback).
