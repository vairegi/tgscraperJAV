"""session_manager.py — multi-account rotation.
Env: STRING_SESSIONS="sess1,sess2,..." (or single STRING_SESSION).
The scraper runs POSTS_PER_ACCOUNT posts on one account, then rotates to the
next while the first rests. On FloodWait it rotates immediately. Progress
lives in MongoDB keyed by target channel, so the next account continues
exactly where the previous one stopped."""
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH, SESSIONS


class SessionManager:
    def __init__(self):
        self._clients = []
        self._idx = 0

    async def start(self):
        for sess in SESSIONS:
            c = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await c.start()
            self._clients.append(c)
        return self._clients[0]

    def count(self):
        return len(self._clients)

    def current(self):
        return self._clients[self._idx]

    def current_name(self):
        return f"acc{self._idx + 1}/{len(self._clients)}"

    def rotate(self):
        self._idx = (self._idx + 1) % len(self._clients)
        return self.current()

    def all(self):
        return list(self._clients)
