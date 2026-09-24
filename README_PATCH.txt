
================================================================================
README_PATCH — v42: caption download links + /keepimages + restricted-content
cover workaround + /targets board TTL/invalidation
================================================================================

DRAG onto the repo root: bot.py, flow.py, scraper.py, db.py, botapi.py,
richboard.py, forwarder.py, README.md, README_PATCH.txt
(everything else unchanged — forwarder.py IS in this zip because the
restricted-cover workaround lives in send_cover).

1) EMBEDDED CAPTION LINK SUPPORT. The /target wizard now asks (after DB and
   DB2): "Is the download link in a 'Button' or in the 'Caption'?" Caption
   mode asks for the EXACT text holding the hidden hyperlink (e.g. "Download
   Here") and stores link_mode + link_trigger per target in MongoDB
   (db.get_targets auto-migrates old docs to mode 'button'). Detection and
   scraping are mode-aware:
   - scraper.is_post(msg, link_mode, link_trigger) — caption mode = media +
     caption + the trigger text carrying a MessageEntityTextUrl (no buttons
     needed); button mode is byte-identical behavior to before.
   - scraper.find_caption_url() matches the entity's covered text EXACTLY
     after the same NFKD Unicode fold the button matcher uses, so
     '𝗗𝗼𝘄𝗻𝗹𝗼𝗮𝗱 𝗛𝗲𝗿𝗲' == 'Download Here'.
   - flow.process_post caption path: a t.me/<bot>?start=… caption link runs
     the SAME deep-link -> Short-link -> bypass chain as a Download button;
     any other URL is treated as the short link itself and jumps straight
     into _bypass_chain (link_bot may be None there — the bypass result
     resolves it; the group path's base_f moved AFTER the Open-link click
     for exactly this reason).
   - NEW COMMAND /targatelinkmode <n> button | caption <trigger text> —
     change an existing target's mode without re-adding it (progress kept).

2) /keepimages on|off — GLOBAL Mongo-backed switch (default ON = old
   behavior). OFF: flow's classification discards EVERY image the media bot
   sends (photos AND image-mime documents, with or without caption) before
   the DB bundle is built. Videos, .srt and text notes are never touched;
   the video-armed quiet timer in _collect_media is unchanged.

3) RESTRICTED TARGETS ("Restrict saving content" / msg.noforwards).
   forwarder.send_cover detects the flag and, instead of a by-reference
   copy (which Telegram rejects), downloads the cover to a temp file,
   uploads it to the DB channel as a NEW message with the exact original
   caption/buttons/spoiler, then DELETES the temp file in a finally block —
   success or failure — so Render's disk never fills. Media from the bypass
   bot is unaffected (it isn't restricted), and the fresh DB cover post
   mirrors to DB2 normally.

4) /targets BOARD LIFECYCLE. BOARD_TTL 150s -> 1800s (30 min). mark_board
   now ZEROES the previous board's timestamp when a new board is sent for
   the same chat (deleting it would fall through to the live-board check
   and wrongly allow stale taps), so buttons on an older scrolled-up board
   answer "This board has expired" and change nothing. In-place edits
   (Refresh/toggle) pass the same message id and are never invalidated.

TESTING: py_compile PASS on all 7 changed .py files. Mocked sandbox suite
(55+ assertions): caption URL extraction (exact/fancy-font/wrong-trigger/
no-entity), mode-aware is_post/why_not_post, is_image_msg, /keepimages
on/off classification, caption-mode process_post end-to-end (deep-link AND
direct-short-link forms) with a mocked client, button-mode process_post
regression, restricted cover download->upload->temp-deleted (plus deletion
on upload failure, and the normal non-restricted path never downloading),
board TTL=1800 + previous-board invalidation + in-place edit survival, db
target migration/add/set_target_link_mode/keep_images defaults, and botapi
handler registration + admin-gate + wizard-step wiring. NO live Telegram
calls were made — validate one real caption-mode post on a test target
after deploy.
================================================================================

================================================================================
README_PATCH — v41: /stats v2 + /removeavoid N + replace-wins guarantee
================================================================================

DRAG onto the repo root: botapi.py, README_PATCH.txt (everything else unchanged).

1) /stats v2 — per userbot: connection state, name/@username/id, then a full
   membership matrix: ✅ member / ❌ NOT a member / 👑 admin for EVERY target
   channel and every DB + DB2 channel, plus the CONTROL BOT's own DB/DB2
   rights. A ❌ on a target is exactly why a resume silently does nothing —
   join it with /invite.

2) /removeavoid N — N is the entry number in the /avoidtext list (per-target
   avoids numbered top to bottom). Bare /removeavoid shows the numbered list.

3) /replaceword always wins over /avoid and /avoidtext — replacement rules
   run BEFORE any strip in the DB2 mirror pipeline (order verified in code).

TESTING: py_compile PASS on botapi.py; role-check and numbering logic match
patterns already proven in v38–v40.
================================================================================
