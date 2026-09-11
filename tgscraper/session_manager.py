"""session_manager.py — 1 session today, N sessions tomorrow.
To go multi-account later: add more StringSessions to SESSIONS, the
engine will round-robin acquire() — no refactor needed."""
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH, STRING_SESSION

class SessionManager:
    def __init__(self):
        self._clients = []
        self._rr = 0

    async def start(self):
        c = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
        await c.start()
        self._clients.append(c)
        return c

    def primary(self):
        return self._clients[0]

    def acquire(self):  # future multi-session round-robin
        c = self._clients[self._rr % len(self._clients)]
        self._rr += 1
        return c
