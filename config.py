import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", "")

if not API_ID:
    raise ValueError("API_ID is missing in .env")

if not API_HASH:
    raise ValueError("API_HASH is missing in .env")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing in .env")

if not ASSISTANT_SESSION:
    raise ValueError("ASSISTANT_SESSION is missing in .env")
