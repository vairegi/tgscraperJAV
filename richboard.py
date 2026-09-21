"""richboard.py — Bot API rich-message "charge sheet" board (v37).

Telethon (MTProto) can't render tables or colored inline buttons — those are
Bot API features (rich messages: sendRichMessage, Bot API 10.1+; button style:
danger/success/primary, Bot API 9.4+). This module is a thin aiohttp client on
top of the existing BOT_TOKEN that ONLY sends messages — it never polls, so the
Telethon control bot keeps receiving updates (incl. the board's button taps via
events.CallbackQuery) exactly as before.

Usage:
    ok = await richboard.send_targets_board(chat_id, rows)
    # ok=False  -> feature unsupported / send failed -> caller falls back to
    #              the plain markdown /targets listing.

rows = [{
    "n": 1,                       # target number (1-based)
    "title": "Channel title",     # target title (plain text)
    "t_link": "https://t.me/...", # target link (t.me/c/<id>/<last_post>) or None
    "db_title": "DB title", "db_link": "https://t.me/+...",   # or None
    "db2_title": "DB2 title", "db2_link": "https://t.me/+...",# or None
    "resume": 372,                # resume message id
    "paused": False,
}, ...]

Board layout:
    heading: 🎯 TARGETS
    table:   N | Target | DB | DB2 | Resume   (linked titles, ⏸ marks paused)
    buttons: one row per target -> Pause/Resume toggle (danger/success colour),
             bottom row -> 🔄 Refresh (primary).
Callback data: "tglp:t:<n>" (toggle target n), "tglp:refresh".
"""
import asyncio
import json
import logging

import aiohttp

from config import BOT_TOKEN

log = logging.getLogger("richboard")

_API = "https://api.telegram.org/bot{tok}/{method}"
_TIMEOUT = aiohttp.ClientTimeout(total=20)

CB_PREFIX = "tglp:"          # callback namespace for this module
CB_REFRESH = CB_PREFIX + "refresh"
CB_TOGGLE = CB_PREFIX + "t:"  # + <n>


# ---------------------------------------------------------------------------
# low-level Bot API call (send-only; never long-polls)
# ---------------------------------------------------------------------------
async def api_call(method, payload):
    """POST one Bot API method. Returns (ok: bool, result_or_error)."""
    url = _API.format(tok=BOT_TOKEN, method=method)
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as sess:
            async with sess.post(url, json=payload) as resp:
                data = await resp.json(content_type=None)
    except Exception as e:
        log.warning("Bot API %s failed to send: %s", method, e)
        return False, str(e)
    if not data.get("ok"):
        log.warning("Bot API %s rejected: %s", method, data.get("description"))
        return False, data.get("description", "unknown error")
    return True, data.get("result")


# ---------------------------------------------------------------------------
# rich text helpers (RichText can be a plain string, an array, or typed objs)
# ---------------------------------------------------------------------------
def _rt_text(title, url=None):
    """Linked rich-text cell content: RichTextUrl or plain string."""
    title = (title or "—").replace("\n", " ").strip() or "—"
    if url:
        return {"type": "url", "text": title, "url": url}
    return title


def _cell(title, url=None, header=False, align="left"):
    c = {"align": align}
    if title is not None or url is not None:
        c["text"] = _rt_text(title, url)
    if header:
        c["is_header"] = True
    return c


# ---------------------------------------------------------------------------
# board construction
# ---------------------------------------------------------------------------
def build_targets_payload(chat_id, rows):
    """Full sendRichMessage payload for the targets charge sheet."""
    header = [
        _cell("N", header=True, align="center"),
        _cell("Target", header=True),
        _cell("DB", header=True),
        _cell("DB2", header=True),
        _cell("Resume", header=True, align="right"),
    ]
    cells = [header]
    buttons = []
    for r in rows:
        flag = "⏸ " if r.get("paused") else ""
        cells.append([
            _cell(str(r["n"]), align="center"),
            _cell(flag + (r.get("title") or str(r.get("id", "?"))), r.get("t_link")),
            _cell(r.get("db_title") or "(fallback /adddb)", r.get("db_link")),
            _cell(r.get("db2_title") or "—", r.get("db2_link")),
            _cell(str(r.get("resume", 0)), align="right"),
        ])
        if r.get("paused"):
            buttons.append([{"text": f"▶️ Resume {r['n']}",
                             "style": "success",
                             "callback_data": f"{CB_TOGGLE}{r['n']}"}])
        else:
            buttons.append([{"text": f"⏸ Pause {r['n']}",
                             "style": "danger",
                             "callback_data": f"{CB_TOGGLE}{r['n']}"}])
    buttons.append([{"text": "🔄 Refresh", "style": "primary",
                     "callback_data": CB_REFRESH}])

    return {
        "chat_id": chat_id,
        "rich_message": {
            "blocks": [
                {"type": "heading", "size": 3,
                 "text": "🎯 TARGETS — live board"},
                {"type": "table",
                 "is_bordered": True, "is_striped": True, "is_compact": True,
                 "cells": cells},
                {"type": "footer",
                 "text": "DB = storage · DB2 = clean mirror · tap a button to "
                         "pause/resume · /targets_text for the plain list"},
            ],
        },
        "reply_markup": {"inline_keyboard": buttons},
    }


async def send_targets_board(chat_id, rows, retries=2):
    """Send the targets charge sheet. Returns True on success, False to fall
    back to the plain markdown listing (unsupported Bot API, old server, etc)."""
    if not BOT_TOKEN or not rows:
        return False
    payload = build_targets_payload(chat_id, rows)
    for attempt in range(retries + 1):
        ok, res = await api_call("sendRichMessage", payload)
        if ok:
            return True
        desc = str(res)
        # hard failures that retrying won't fix (old Bot API server, rich
        # messages unsupported, chat can't be reached) -> fall back at once
        if any(k in desc.lower() for k in
               ("rich", "unsupported", "can't parse", "chat not found",
                "forbidden", "not found", "bad request")):
            log.warning("rich board unsupported (%s) — markdown fallback", desc)
            return False
        await asyncio.sleep(1.5 * (attempt + 1))
    return False


def parse_callback(data):
    """Decode a board button tap. Returns ('toggle', n) | ('refresh', None) |
    (None, None) if it's not ours."""
    if not data or not data.startswith(CB_PREFIX):
        return None, None
    rest = data[len(CB_PREFIX):]
    if rest == "refresh":
        return "refresh", None
    if rest.startswith("t:") and rest[2:].isdigit():
        return "toggle", int(rest[2:])
    return None, None
