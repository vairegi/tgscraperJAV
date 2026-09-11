"""scraper.py — smart post detection + button/link helpers."""
import re
from config import BTN_DOWNLOAD

def find_button(msg, needle):
    """Return (row, col, button) whose text CONTAINS needle (case-insensitive)."""
    needle = needle.lower()
    for r, row in enumerate(msg.buttons or []):
        for c, b in enumerate(row):
            if needle in (getattr(b, "text", "") or "").lower():
                return r, c, b
    return None

def is_post(msg):
    """A real post = PHOTO + CAPTION + INLINE BUTTONS incl. a 'Download' button.
    Skips service messages, text-only junk, polls, media w/o buttons."""
    if not msg or getattr(msg, "action", None) is not None:
        return False
    if not msg.photo:
        return False
    if not (msg.message or "").strip():
        return False
    if not msg.buttons:
        return False
    return find_button(msg, BTN_DOWNLOAD) is not None

TG_LINK_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)\?start=([A-Za-z0-9_\-]+)")
URL_RE = re.compile(r"https?://\S+")

def parse_tg_start(text):
    m = TG_LINK_RE.search(text or "")
    return (m.group(1), m.group(2)) if m else (None, None)

def first_url(text):
    m = URL_RE.search(text or "")
    return m.group(0) if m else None
