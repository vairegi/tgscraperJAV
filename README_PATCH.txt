================================================================================
README_PATCH — v30: skip bot echo-images, resume off-by-one fix,
multi-word /avoidtext
================================================================================

DRAG onto the repo root: flow.py, bot.py, botapi.py, README_PATCH.txt
(db.py / README.md / mtprotomgr.py unchanged from v29.)

1) SKIP BOT ECHO-IMAGES (flow.py)
   Some target bots re-send the cover image INSIDE the media batch, with the
   SAME caption as the target channel's cover post. That image was already
   delivered by send_cover(), so the DB got the identical cover image twice.
   Now: any collected message that (a) is a photo and (b) has a caption that
   matches the cover post's caption (normalized compare, either direction)
   is skipped — it is NOT forwarded to the DB channel. Videos, .srt files,
   text notes (like deletion warnings) and any genuinely different images
   are still mirrored exactly as before.

2) RESUME OFF-BY-ONE FIX (bot.py)
   Bug: progress was saved as msg.id AFTER a post finished, but
   iter_messages(min_id=...) is EXCLUSIVE — so after a pause/restart the
   scan resumed one message too late. (Scraped up to 65 -> resume -> 66 got
   skipped, 67 scraped instead.)
   Fix: on success the scraper now saves msg.id - 1. The next pass starts AT
   the just-finished post, re-detects it, and moves past it — so the truly
   next post (66) is never skipped.

3) MULTI-WORD /avoidtext (botapi.py)
   Before, only a single word (or properly quoted text) worked. Now
   everything after the target number is the string to strip, quoted OR not:
       /avoidtext 1 "how are you"
       /avoidtext 1 how are you
   both store "how are you".

   MANAGING THE LIST (already built in, unchanged):
       /avoidtext            — list avoid-strings for ALL targets
       /avoidtext 2          — list them for target 2 (numbered)
       /removeavoid 2 1      — remove string #1 from target 2

TESTING (sandbox mocks, no live Telegram): py_compile PASS on all changed
files; behavior tests 15 PASS / 0 FAIL (echo image skipped, video/srt/notes/
different photos kept, empty-caption edge case, progress saved as id-1 and
resume scan covering the next post, quoted + unquoted multi-word avoid
parsing, all three code changes present in the shipped files).
NOTE: after this deploy, each channel's saved progress is one less than
before — the first scan re-checks the last completed post once (it is
detected as already-scraped and skipped), then continues normally.
================================================================================
