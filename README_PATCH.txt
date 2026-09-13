TGSCRAPER v22 PATCH — 3 changed files (overwrite, push, redeploy)
=================================================================
  flow.py    — bypass step now works with either a GROUP (button reply,
               unchanged) or a BOT (text-only reply, new).
  botapi.py  — /bypass wizard accepts a bot @username; validates
               group-vs-bot and confirms which kind was saved.
  README.md  — updated /bypass row + brief docs.
  README_PATCH.txt — this file.

WHY (your request): the bypass group is unusable, so bypass moves to
@dex_fekkyeww_bot (or any similar bypass bot). That bot's reply has NO
"Open link" button — it's a formatted DM like:

  ◈ 𝑶𝒓𝒊𝒈𝒊𝒏𝒂𝒍 𝑳𝒊𝒏𝒌
  ➤ https://remso.xyz/EDtazEpz
  ◈ 𝑩𝒚𝒑𝒂𝒔𝒔𝒆𝒅 𝑳𝒊𝒏𝒌
  ➤ https://t.me/Fubuki_xRobot?start=<payload>
  Developed by @nexunx

The bypassed link is what the userbot needs — it's the LAST t.me/... URL
in the message (username credits like "@nexunx" are not t.me URLs, so
they're ignored automatically).

HOW /bypass NOW BEHAVES:
  /bypass @dex_fekkyeww_bot   → saved as BOT endpoint, reply reads:
     "✅ Saved bypass = @dex_fekkyeww_bot (bot). No 'Open link' button
      needed — the bypassed t.me link is read from the reply text."
  /bypass -100…               → saved as GROUP endpoint (unchanged),
     reply reads: "... The tagger's 'Open link' button reply is expected."
  Anything that resolves to a user/non-bot or a channel-only entity is
  rejected with a helpful message.

HOW THE FLOW BRANCHES (per post, step 4):
  - Resolves the bypass entity via client.get_entity() ONCE.
  - If it's a BOT: sends the short link, waits (WAIT_BYPASS_REPLY, def
    60s) for a reply containing "t.me/", harvests all t.me URLs from the
    text with a regex, PREFERS a "?start=…" deep link (that's always the
    bypassed one), falls back to any t.me URL. Then fires /start
    <payload> at that link's target bot — exactly what clicking "Open
    link" used to do.
  - If it's a GROUP: unchanged — waits for the tagged 'Open link' button
    reply, clicks it (still handles Open link routing to a different bot
    if that ever happens).

Everything downstream (step 6 LINK_BOT final reply → MEDIA_BOT videos →
DB channel verified delivery) is IDENTICAL for both bypass kinds.

TESTS (sandbox, mock Telethon — no network):
  - py_compile all 10 files: OK
  - URL harvest regex: parses your exact example message, picks the
    Fubuki?start=… link (skips remso.xyz + @nexunx credit); schemeless
    "t.me/…" also matches: PASS
  - Integration bot-bypass: short link sent to bypass bot → BYPASSED
    payload fired at LINK_BOT → MEDIA_BOT reached → video delivered to
    DB channel: PASS
  - Integration group-bypass (regression): unchanged behavior still
    works — Open link button clicked with OPEN payload → LINK_BOT →
    MEDIA_BOT: PASS
  - 12 PASS / 0 FAIL. NOT live-tested (no session in sandbox).

AFTER DEPLOY:
  1. /bypass @dex_fekkyeww_bot   (confirm the "bot" line in the reply)
  2. /resume                     (or /start)
  3. Watch Render logs for "post N: bypass bot returned deep link -> @<LINK_BOT>"
     followed by the normal step 6 / 7 / delivery lines.
