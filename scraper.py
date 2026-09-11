"""scraper.py — smart post detection + button/link helpers.
v4: post = has media (photo OR spoiler OR any attachment) + caption + inline
buttons incl. 'Download'. Spoiler images arrive as documents/web-preview, not
plain photos — the old photo-only check skipped every real post."""
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
    """A real post = MEDIA + CAPTION + INLINE BUTTONS incl. a 'Download' button.
    Media counts as photo, document (spoiler images arrive this way), video,
    or web-preview. Skips service messages and text-only junk."""
    if not msg or getattr(msg, "action", None) is not None:
        return False
    if not (msg.photo or msg.document or msg.video or msg.web_preview):
        return False
    if not (msg.message or "").strip():
        return False
    if not msg.buttons:
        return False
    return find_button(msg, BTN_DOWNLOAD) is not None


def why_not_post(msg):
    """Human-readable reason a message was skipped (for logging)."""
    if not msg:
        return "empty"
    if getattr(msg, "action", None) is not None:
        return "service message"
    if not (msg.photo or msg.document or msg.video or msg.web_preview):
        return "no media"
    if not (msg.message or "").strip():
        return "no caption"
    if not msg.buttons:
        return "no buttons"
    if find_button(msg, BTN_DOWNLOAD) is None:
        texts = [getattr(b, "text", "") for row in (msg.buttons or []) for b in row]
        return "no Download button (buttons: " + ", ".join(texts[:4]) + ")"
    return ""


TG_LINK_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)\?start=([A-Za-z0-9_\-]+)")
URL_RE = re.compile(r"https?://\S+")
C_LINK_RE = re.compile(r"(?:https?://)?t\.me/c/(\d+)/(\d+)")


def parse_tg_start(text):
    m = TG_LINK_RE.search(text or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def first_url(text):
    m = URL_RE.search(text or "")
    return m.group(0) if m else None


def parse_private_link(text):
    """t.me/c/<channel>/<msg> -> (chat_id=-100<channel>, msg_id)."""
    m = C_LINK_RE.search(text or "")
    return (int("-100" + m.group(1)), int(m.group(2))) if m else (None, None)
