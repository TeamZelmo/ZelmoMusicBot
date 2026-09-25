"""
Ye script sirf EK BAAR chalao taaki assistant (userbot) account ka
SESSION_STRING mil jaye. Ye session string .env file me SESSION_STRING
ke saamne paste kar dena.

IMPORTANT: Ye assistant account (aapka normal Telegram account, bot nahi)
hona chahiye kyunki bots VC me khud join nahi kar sakte - isiliye VC join
karne ke liye ek userbot (assistant) chahiye hota hai.

Chalane ka tarika:
    python generate_session.py
"""

from pyrogram import Client

API_ID = int(input("API_ID daalo: "))
API_HASH = input("API_HASH daalo: ")

with Client(name="assistant_session", api_id=API_ID, api_hash=API_HASH, in_memory=True) as app:
    session_string = app.export_session_string()
    print("\n\n=== Ye raha aapka SESSION_STRING (.env me daalo) ===\n")
    print(session_string)
    print("\n=====================================================\n")
