import os

from dotenv import load_dotenv


load_dotenv()


API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", "").strip()


if API_ID == 0:
    raise RuntimeError("API_ID is missing.")

if not API_HASH:
    raise RuntimeError("API_HASH is missing.")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")

if not ASSISTANT_SESSION:
    raise RuntimeError("ASSISTANT_SESSION is missing.")
