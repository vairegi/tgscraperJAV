"""
config.py — all env-driven settings. Nothing sensitive hardcoded.
Render env vars: API_ID, API_HASH, STRING_SESSION, MONGO_URI
"""
import os

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
STRING_SESSION = os.environ.get("STRING_SESSION", "")
# multi-account: comma-separated sessions; falls back to the single one
SESSIONS = [x.strip() for x in os.environ.get("STRING_SESSIONS", "").split(",") if x.strip()]
if not SESSIONS:
    if not STRING_SESSION:
        raise KeyError("Set STRING_SESSION or STRING_SESSIONS")
    SESSIONS = [STRING_SESSION]
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

# Health-check server
PORT = int(os.environ.get("PORT", "10000"))  # Render injects PORT

# Button texts (case-insensitive partial match)
BTN_DOWNLOAD = "download"
BTN_SHORT_LINK = "short link"
BTN_OPEN_LINK = "open link"

# Known bots in the chain
FUBUKI_BOT = os.environ.get("FUBUKI_BOT", "@Fubuki_xRobot")
