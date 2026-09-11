TGSCRAPER v6 PATCH — 1 changed file (overwrite, push, redeploy)
================================================================
  flow.py — THE DOWNLOAD-CLICK FIX. Clicking '𝗗𝗼𝘄𝗻𝗹𝗼𝗮𝗱' on a post used to
  fire a BARE /start at @Fubuki_xRobot (no payload) -> Fubuki answered with
  its generic welcome ('Hey there! ... Help / Close' — exactly your
  screenshot) and the flow timed out ('no matching reply in chat
  @Fubuki_xRobot within 20s', posts 11,12,13,14...).

  New _follow_button():
   - URL button deep-linking to a bot -> sends '/start <payload>' to that bot
     (this is what makes Fubuki serve the linked content, not the welcome)
   - plain URL button -> returns the URL (Short link capture)
   - callback button -> real msg.click()
  Reply accepted as 'Short link' BUTTON or plain-text link. If Fubuki shows
  its welcome first, the flow nudges with one more /start and retries once;
  total silence becomes a clean named failure (no crash, post is skipped and
  logged in /progress). 'Open link' in the bypass group uses the same helper.

AFTER DEPLOY:
  /reset -> /goto https://t.me/c/2514892126/11 -> /start
  Expected log: POST FOUND -> clicking Download -> waiting Fubuki ->
  getting short link -> waiting bypass group Open link -> ...
