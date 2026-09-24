"""scraper.py — smart post detection + button/link helpers.
v5: Unicode-normalized button matching — channel/bot buttons use Mathematical
Bold Sans-Serif (e.g. '𝗗𝗼𝘄𝗻𝗹𝗼𝗮𝗱'), which never matches plain 'download'.
NFKD folds all fancy-font variants (bold/italic/serif/mono/fullwidth) to ASCII,
so Download / Short link / Open link match everywhere regardless of styling.
v42: caption-mode posts — some channels hide the download URL as an embedded
hyperlink (MessageEntityTextUrl) behind a trigger text in the caption instead
of an inline Download button; find_caption_url extracts it, and is_post /
why_not_post take the target's link_mode so detection works either way."""
import re
import unicodedata
from config import BTN_DOWNLOAD
from telethon.tl.types import MessageEntityTextUrl  # v42: embedded caption links


def norm(text):
    """Fold fancy Unicode fonts (𝗯𝗼𝗹𝗱, 𝘪𝘵𝘢𝘭𝘪𝘤, ｆｕｌｌｗｉｄｔｈ...) to
    lowercase ASCII-ish text for matching. Emoji and symbols are kept but
    ignored by 'in' matching on plain needles."""
    if not text:
        return ""
    return unicodedata.normalize("NFKD", text).lower()


def find_button(msg, needle):
    """Return (row, col, button) whose normalized text CONTAINS the needle.
    needle may be a single string or a list of aliases — the first alias
    found wins (custom /linkbutton labels are passed as a list)."""
    needles = [needle] if isinstance(needle, str) else list(needle)
    needles = [norm(n) for n in needles if n]
    for r, row in enumerate(msg.buttons or []):
        for c, b in enumerate(row):
            t = norm(getattr(b, "text", ""))
            if any(n in t for n in needles):
                return r, c, b
    return None


def find_caption_url(msg, trigger):
    """v42: the hidden URL behind the EXACT trigger text in a caption.
    Uses Telethon's entity-aware get_entities_text (correct UTF-16 offsets),
    matches the covered text EXACTLY after the same Unicode fold (norm) the
    button matcher uses — so '𝗗𝗼𝘄𝗻𝗹𝗼𝗮𝗱 𝗛𝗲𝗿𝗲' == 'Download Here'.
    Returns the embedded URL, or None."""
    if not trigger:
        return None
    needle = norm(trigger).strip()
    for ent, text in (msg.get_entities_text() or []):
        if isinstance(ent, MessageEntityTextUrl) and norm(text).strip() == needle:
            return getattr(ent, "url", None)
    return None


def is_post(msg, link_mode="button", link_trigger=None):
    """A real post = MEDIA + CAPTION + a download link.
    Button mode (default): INLINE BUTTONS incl. a 'Download' button.
    Caption mode (v42): no buttons needed — the exact trigger text must carry
    a hidden hyperlink (MessageEntityTextUrl). Skips service messages and
    text-only junk either way."""
    if not msg or getattr(msg, "action", None) is not None:
        return False
    if not (msg.photo or msg.document or msg.video or msg.web_preview):
        return False
    if not (msg.message or "").strip():
        return False
    if link_mode == "caption":
        return find_caption_url(msg, link_trigger) is not None
    if not msg.buttons:
        return False
    return find_button(msg, BTN_DOWNLOAD) is not None


def why_not_post(msg, link_mode="button", link_trigger=None):
    """Human-readable reason a message was skipped (for logging)."""
    if not msg:
        return "empty"
    if getattr(msg, "action", None) is not None:
        return "service message"
    if not (msg.photo or msg.document or msg.video or msg.web_preview):
        return "no media"
    if not (msg.message or "").strip():
        return "no caption"
    if link_mode == "caption":  # v42
        if find_caption_url(msg, link_trigger) is None:
            return f"no embedded caption link matching trigger {link_trigger!r}"
        return ""
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


def is_image_msg(m):
    """v42: True for photos AND image-mime documents (spoiler images arrive
    as documents). Videos are excluded — a video carries a document too."""
    if is_video_msg(m):
        return False
    return bool(getattr(m, "photo", None)) or (
        getattr(m, "document", None) is not None and
        (getattr(m.document, "mime_type", "") or "").lower().startswith("image/"))


# ---------------- v39: short-link domain extraction + rule matching ----------------
# Used by flow.py's bypass step: a captured short link's domain decides WHICH
# bypass endpoint handles it (Mongo-backed rules from /domainbypass).

_DOMAIN_RE = re.compile(r"^(?:https?://)?(?:www\.)?([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)")


def extract_domain(url):
    """Short-link URL -> bare lowercase domain ('https://www.babylinks.in/x?y'
    -> 'babylinks.in'). None when there is no dotted host."""
    m = _DOMAIN_RE.match((url or "").strip())
    return m.group(1).lower() if m else None


def norm_domain(text):
    """Normalize a domain OR a URL to its bare domain form, so /domainbypass
    accepts both 'babylinks.in' and a pasted link."""
    return extract_domain(text) or (text or "").strip().lower()


def match_domain_rule(url, rules):
    """First matching rule's endpoint for this short-link URL, else None.
    Rule domains are bare ('babylinks.in') or wildcard ('aerolinks.*' —
    matches any aerolinks TLD). A rule domain equal to the link's registrable
    tail also matches (so 'babylinks.in' matches 'go.babylinks.in')."""
    host = extract_domain(url)
    if not host:
        return None
    for r in rules or []:
        dom = (r.get("domain") or "").lower()
        if not dom:
            continue
        if dom.endswith(".*"):
            if host == dom[:-2] or host.startswith(dom[:-1]):
                return r.get("endpoint")
        elif host == dom or host.endswith("." + dom):
            return r.get("endpoint")
    return None
