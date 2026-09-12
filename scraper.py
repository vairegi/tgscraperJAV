"""scraper.py — smart post detection + button/link helpers.
v5: Unicode-normalized button matching — channel/bot buttons use Mathematical
Bold Sans-Serif (e.g. '𝗗𝗼𝘄𝗻𝗹𝗼𝗮𝗱'), which never matches plain 'download'.
NFKD folds all fancy-font variants (bold/italic/serif/mono/fullwidth) to ASCII,
so Download / Short link / Open link match everywhere regardless of styling."""
import re
import unicodedata
from config import BTN_DOWNLOAD


def norm(text):
    """Fold fancy Unicode fonts (𝗯𝗼𝗹𝗱, 𝘪𝘵𝘢𝘭𝘪𝘤, ｆｕｌｌｗｉｄｔｈ...) to
    lowercase ASCII-ish text for matching. Emoji and symbols are kept but
    ignored by 'in' matching on plain needles."""
    if not text:
        return ""
    return unicodedata.normalize("NFKD", text).lower()


def find_button(msg, needle):
    """Return (row, col, button) whose normalized text CONTAINS the needle."""
    needle = norm(needle)
    for r, row in enumerate(msg.buttons or []):
        for c, b in enumerate(row):
            if needle in norm(getattr(b, "text", "")):
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

VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts",
             ".flv", ".wmv", ".mpg", ".mpeg", ".3gp")


def is_video_msg(m):
    """True for ANY video file — incl. .mkv/.avi sent as plain documents,
    which Telethon does NOT expose via m.video."""
    if getattr(m, "video", None):
        return True
    if not getattr(m, "document", None):
        return False
    mime = (getattr(m.document, "mime_type", "") or "").lower()
    if mime.startswith("video/"):
        return True
    name = (getattr(getattr(m, "file", None), "name", "") or "").lower()
    return name.endswith(VIDEO_EXT)


def is_srt_msg(m):
    name = (getattr(getattr(m, "file", None), "name", "") or "").lower()
    return name.endswith(".srt")
