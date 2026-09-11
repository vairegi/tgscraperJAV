"""
config.py — all env-driven settings. Nothing sensitive hardcoded.
Render env vars: API_ID, API_HASH, STRING_SESSION, MONGO_URI
"""
import os

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
STRING_SESSION = os.environ["STRING_SESSION"]
MONGO_URI = os.environ["MONGO_URI"]

# Control bot (BotFather) — powers the tappable "/" command menu
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))  # your numeric TG id

DB_NAME = os.environ.get("MONGO_DB", "tgscraper")

# Timing (seconds) — tune via env if needed
WAIT_BOT_REPLY = int(os.environ.get("WAIT_BOT_REPLY", "20"))      # wait for Fubuki/Rias reply
WAIT_BYPASS_REPLY = int(os.environ.get("WAIT_BYPASS_REPLY", "60"))# wait for bypass group tag
POLL_INTERVAL = 1.5
STEP_DELAY = float(os.environ.get("STEP_DELAY", "2"))    # pause between workflow steps
POST_DELAY = float(os.environ.get("POST_DELAY", "10"))   # pause between posts

# Health-check server
PORT = int(os.environ.get("PORT", "10000"))  # Render injects PORT

# Button texts (case-insensitive partial match)
BTN_DOWNLOAD = "download"
BTN_SHORT_LINK = "short link"
BTN_OPEN_LINK = "open link"

# Known bots in the chain
FUBUKI_BOT = os.environ.get("FUBUKI_BOT", "@Fubuki_xRobot")
