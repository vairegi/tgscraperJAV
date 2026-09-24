"""
config.py — all env-driven settings. Nothing sensitive hardcoded.
Render env vars: API_ID, API_HASH, STRING_SESSION, MONGO_URI
"""
import os

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
STRING_SESSION = os.environ.get("STRING_SESSION", "")

# Multi-account rotation: set STRING_SESSION, STRING_SESSION2, STRING_SESSION3, ...
# as separate Render env vars. They are collected here in order (SESSION first,
# then SESSION2, SESSION3, ...). STRING_SESSIONS (comma-separated) still works
# as a fallback for advanced users.
def _collect_sessions():
    picked = []
    if STRING_SESSION.strip():
        picked.append(STRING_SESSION.strip())
    # numbered slots — probe up to 20, keep in numeric order
    for i in range(2, 21):
        v = os.environ.get(f"STRING_SESSION{i}", "").strip()
        if v:
            picked.append(v)
    # legacy comma-separated fallback
    if not picked:
        picked = [x.strip() for x in os.environ.get("STRING_SESSIONS", "").split(",") if x.strip()]
    return picked

SESSIONS = _collect_sessions()
if not SESSIONS:
    raise KeyError("Set STRING_SESSION (and optionally STRING_SESSION2, STRING_SESSION3, ...)")
POSTS_PER_ACCOUNT = int(os.environ.get("POSTS_PER_ACCOUNT", "20"))  # rotate after N posts
MONGO_URI = os.environ["MONGO_URI"]

# Control bot (BotFather) — powers the tappable "/" command menu
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))  # your numeric TG id

DB_NAME = os.environ.get("MONGO_DB", "tgscraper")

# Timing (seconds) — tune via env if needed
WAIT_BOT_REPLY = int(os.environ.get("WAIT_BOT_REPLY", "20"))      # wait for Fubuki/Rias reply
WAIT_BYPASS_REPLY = int(os.environ.get("WAIT_BYPASS_REPLY", "60"))# wait for bypass group tag
POLL_INTERVAL = 1.5
STEP_DELAY = float(os.environ.get("STEP_DELAY", "5"))    # pause between workflow steps
POST_DELAY = float(os.environ.get("POST_DELAY", "45"))   # pause between posts
# FloodWait cap: waits longer than this -> park (see bot.py)
FLOOD_MAX_WAIT = int(os.environ.get("FLOOD_MAX_WAIT", "1800"))  # 30 min
FLOOD_PARK = int(os.environ.get("FLOOD_PARK", "1800"))          # park duration

# Bulk editing (/replace, /deletetext) pacing — Telegram-safe by default
BULK_EDIT_DELAY = float(os.environ.get("BULK_EDIT_DELAY", "2.5"))  # seconds between edits
BULK_MAX_FLOOD = int(os.environ.get("BULK_MAX_FLOOD", "900"))      # sleep through FloodWait up to this (s)
BULK_PROGRESS_EVERY = int(os.environ.get("BULK_PROGRESS_EVERY", "10"))  # status update cadence

# MTProto bulk jobs (/massdlt, /forward) pacing — Telegram-safe by default.
# Deletes go out in CHUNKS (many ids in one delete call) with a rest between
# chunks, so a 2000-message range is deleted piece-by-piece, never flooded.
MASS_DELETE_CHUNK = int(os.environ.get("MASS_DELETE_CHUNK", "100"))   # ids per delete call
MASS_DELETE_DELAY = float(os.environ.get("MASS_DELETE_DELAY", "3"))   # seconds between chunks
FORWARD_DELAY = float(os.environ.get("FORWARD_DELAY", "4"))
# v44: /forward hands off to the next userbot after N messages (a
# FloodWait rotates immediately and the flooded account rests).
FORWARD_SWITCH_EVERY = int(os.environ.get("FORWARD_SWITCH_EVERY", "200"))
# v44.1: consecutive media messages are sent in BATCHES of N (one
# paced group send instead of N individual ones) — much faster.
FORWARD_BATCH = int(os.environ.get("FORWARD_BATCH", "10"))

# Health-check server
PORT = int(os.environ.get("PORT", "10000"))  # Render injects PORT

# Button texts (case-insensitive partial match)
BTN_DOWNLOAD = "download"
BTN_SHORT_LINK = "short link"
BTN_OPEN_LINK = "open link"

# Known bots — v20: OPTIONAL fallbacks only. flow.py now discovers LINK_BOT
# from each post's Download-button URL and MEDIA_BOT from LINK_BOT's final
# reply, so different targets can use different bot pairs with no config.
# These env vars stay as a safety net for weird posts whose Download button
# isn't a t.me deep link.
FUBUKI_BOT = os.environ.get("FUBUKI_BOT", "") or None
MEDIA_BOT = os.environ.get("MEDIA_BOT", "") or None
