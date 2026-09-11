TGSCRAPER v5 PATCH — 2 changed files (overwrite, push, redeploy)
================================================================
  1. scraper.py — THE FONT FIX. Your channel's buttons are in Unicode
     Mathematical Bold Sans-Serif: '𝗗𝗼𝘄𝗻𝗹𝗼𝗮𝗱' / '𝗛𝗼𝘄 𝘁𝗼 𝗮𝗰𝗰𝗲𝘀𝘀 𝗹𝗶𝗻𝗸'
     (confirmed in your Render log). Those glyphs are NOT the letters
     D-o-w-n..., so plain matching could never find them. New norm() folds
     ALL fancy fonts (bold/italic/serif/mono/fullwidth) to ASCII via NFKD,
     then matches case-insensitively. Button matching everywhere now uses it.
  2. flow.py    — the same normalization is applied to reply-text matching
     (e.g. Fubuki's 'Here is your link' message), so styled text in later
     workflow steps can't break the chain either.

AFTER DEPLOY:
  /reset -> /goto https://t.me/c/2514892126/11 -> /start
  Render log should now show 'POST FOUND: msg 11' and the Download flow.

Note: posts whose ONLY button is '𝗕𝗨𝗬 𝗦𝗨𝗕𝗦𝗖𝗥𝗜𝗣𝗧𝗜𝗢𝗡' (2 seen in your log)
are correctly skipped — they have no Download button.
