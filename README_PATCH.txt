TGSCRAPER v3.3 PATCH — 2 changed files only
===========================================
Overwrite in repo root, push, Render redeploys:

  1. botapi.py — NEW /reset   : clears progress -> next scan starts from POST 1
                 NEW /goto <id>: start scraping from a specific message id
                 /lastpost now shows channel overview: messages scanned,
                 newest post id, caption, Download button check, AND your
                 current resume point (so you can see the mismatch yourself).
                 Command menu updated (now 15 commands).
  2. db.py     — adds reset_progress() helper.

WHY THE BOT WAS "WAITING FOR NEW POSTS":
Your Mongo already had progress saved at message 346 (from the earlier buggy
runs), and your channel's newest message is ~346 — so the scraper correctly
resumed past the end. It was standing at the finish line, not broken.

FIX ON YOUR SIDE (10 seconds):
  In @scrapjavbot send:  /reset     then   /start
  -> Render log will show: scanning target ... from message id 0
  -> it will now process post 1, 2, 3 ... (150+ posts)
To start from a specific post instead: /goto <message_id> then /start.
Use /lastpost to see real message ids in the channel.
