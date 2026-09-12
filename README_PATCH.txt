TGSCRAPER v15 PATCH — 2 changed files (overwrite, push, redeploy)
=================================================================
  forwarder.py — ACC2 MediaEmptyError FIXED. File references (msg.media)
  are bound to the account that received them; acc2 re-sending acc1-
  fetched media got MediaEmptyError on every post (your log: 99-114, all
  at send_cover). Now media is downloaded to BYTES first, then re-uploaded
  — no account binding, any rotating account can send. Tag-free copy mode,
  spoiler + buttons + albums preserved, direct-resend fallback kept.

  flow.py — MEDIA BOT NOW COLLECTS EVERYTHING: videos (any format incl
  .mkv), .srt, images, stickers, other documents — all go to the DB
  channel. Stats gain 'other_sent'. (Also fixes an 'other' NameError that
  slipped into the split block.)

NOTE: bytes re-upload = a bit slower per post, but works on every account.

AFTER DEPLOY — re-run the failed acc2 posts:
  /goto https://t.me/c/<channel>/99   then   /start
