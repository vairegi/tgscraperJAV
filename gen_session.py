"""gen_session.py — run ONCE locally (not on Render) to create a StringSession
for each account you want to add:
    pip install telethon==1.40.0
    python gen_session.py
Paste the printed string into Render env STRING_SESSIONS (comma-separated).
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API_ID: "))
api_hash = input("API_HASH: ")
with TelegramClient(StringSession(), api_id, api_hash) as c:
    print("\n=== YOUR STRING SESSION (keep it secret) ===")
    print(c.session.save())
