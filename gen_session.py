# Helper (assistant) account ka SESSION_STRING banane ke liye ek baar chalao
import asyncio
from pyrogram import Client

API_ID = int(input("API_ID: "))
API_HASH = input("API_HASH: ")

async def main():
    async with Client("gen", api_id=API_ID, api_hash=API_HASH, in_memory=True) as app:
        print("\nAapka SESSION_STRING:\n")
        print(await app.export_session_string())

asyncio.run(main())
