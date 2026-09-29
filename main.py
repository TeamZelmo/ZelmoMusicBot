import asyncio
import html
import json
import logging
import os
import shutil
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from pyrogram import Client, filters, idle as pyro_idle
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from pytgcalls import PyTgCalls, idle, filters as fl
from pytgcalls.types import MediaStream
from ytmusicapi import YTMusic
import yt_dlp

logging.basicConfig(level=logging.WARNING)

# IMPORTANT: Pyrogram Client isi loop ko yaad rakhta hai, isliye Clients se pehle loop banao
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION_STRING = os.getenv("SESSION_STRING")


def _ids(v):
    out = set()
    for x in (v or "").replace(" ", "").split(","):
        if x.lstrip("-").isdigit():
            out.add(int(x))
    return out


# Optional settings (Render Environment me daalo)
OWNERS = _ids(os.getenv("OWNER_ID"))                       # ek ya kai IDs: 123,456
OWNER_USERNAME = (os.getenv("OWNER_USERNAME") or "").lstrip("@")
SUPPORT_LINK = os.getenv("SUPPORT_LINK")                   # https://t.me/yourgroup
UPDATES_LINK = os.getenv("UPDATES_LINK")                   # https://t.me/yourchannel
START_IMAGE = os.getenv("START_IMAGE")                     # direct image URL ya Telegram file_id
BOT_NAME = os.getenv("BOT_NAME", "Music Bot")
START_TIME = time.time()
USERS_FILE = "/tmp/users.json"

# Bot: commands sunta hai | Assistant (helper ID): voice chat me gaana bajata hai
bot = Client("musicbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
assistant = Client("assistant", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
calls = PyTgCalls(assistant)

queues: dict[int, list[dict]] = {}   # chat_id -> [{title, url}, ...]
calls_ready = False
_ytm = None


# ---------------------------------------------------------------- SEARCH / EXTRACT
def _cookie_file():
    """YouTube cookies (optional):
    1) Render Secret File: /etc/secrets/cookies.txt
    2) ya cookies.txt file project folder me
    3) ya Environment variable COOKIES (poora cookies.txt ka text)
    """
    dst = "/tmp/cookies.txt"
    for src in ("/etc/secrets/cookies.txt", "cookies.txt"):
        if os.path.exists(src):
            shutil.copy(src, dst)        # yt-dlp file me likhta hai, isliye copy
            return dst
    if os.getenv("COOKIES"):
        with open(dst, "w") as f:
            f.write(os.getenv("COOKIES"))
        return dst
    return None


def _opts():
    o = {"format": "bestaudio/best", "quiet": True, "noplaylist": True}
    c = _cookie_file()
    if c:
        o["cookiefile"] = c
    return o


def _ytmusic_search(query: str):
    """YouTube Music se original song dhoondho -> (url, title) ya None"""
    global _ytm
    try:
        if _ytm is None:
            _ytm = YTMusic()
        res = _ytm.search(query, filter="songs", limit=1)
        if res:
            r = res[0]
            artists = ", ".join(a["name"] for a in r.get("artists", []) if a.get("name"))
            title = f'{r["title"]} - {artists}' if artists else r["title"]
            return f'https://www.youtube.com/watch?v={r["videoId"]}', title
    except Exception as e:
        print("ytmusicapi fail:", repr(e), flush=True)
    return None


def _extract(query: str) -> dict:
    """Order: YT Music search -> YouTube search -> SoundCloud search"""
    tries = []   # (target, title_override)
    if query.startswith("http"):
        tries.append((query, None))
    else:
        found = _ytmusic_search(query)
        if found:
            tries.append(found)
        tries.append((f"ytsearch1:{query}", None))
        tries.append((f"scsearch1:{query}", None))

    last_err = None
    for target, title in tries:
        try:
            with yt_dlp.YoutubeDL(_opts()) as ydl:
                info = ydl.extract_info(target, download=False)
                if "entries" in info:
                    info = info["entries"][0]
                return {"title": title or info["title"], "url": info["url"]}
        except Exception as e:
            last_err = e
    raise last_err


async def start_track(chat_id: int) -> bool:
    q = queues.get(chat_id)
    if not q:
        return False
    await calls.play(chat_id, MediaStream(q[0]["url"]))
    return True


# ---------------------------------------------------------------- COMMANDS
@bot.on_message(filters.command("ping"))
async def ping_cmd(_, m: Message):
    await m.reply_text("🏓 Pong! Bot zinda hai.")


# ---------------------------------------------------------------- USERS / OWNER HELPERS
def load_users() -> set:
    try:
        with open(USERS_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def add_user(uid: int):
    users = load_users()
    if uid not in users:
        users.add(uid)
        try:
            with open(USERS_FILE, "w") as f:
                json.dump(list(users), f)
        except Exception:
            pass


def is_owner(uid) -> bool:
    return uid in OWNERS


async def is_authorized(client, m: Message) -> bool:
    """Owner ya group admin hi skip/pause/resume/stop kar sakta hai"""
    if m.from_user is None:                       # anonymous admin
        return m.sender_chat is not None and m.sender_chat.id == m.chat.id
    if is_owner(m.from_user.id):
        return True
    try:
        member = await client.get_chat_member(m.chat.id, m.from_user.id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False


# ---------------------------------------------------------------- START UI (image + buttons)
HELP_TEXT = (
    "📖 <b>Help</b>\n\n"
    "1️⃣ Mujhe group me add karo aur <b>admin</b> banao\n"
    "2️⃣ Helper ID ko bhi group me add karo\n"
    "3️⃣ Group me <b>Voice Chat / Live Stream</b> start karo\n"
    "4️⃣ Likho: /play [gaane ka naam ya link]\n\n"
    "<b>Commands</b>\n"
    "/play - gaana bajao / queue me daalo\n"
    "/queue - queue dekho\n"
    "/skip - agla gaana (admin)\n"
    "/pause - roko (admin)\n"
    "/resume - dobara chalao (admin)\n"
    "/stop - band karo (admin)\n"
    "/ping - bot check"
)


def start_text(name: str) -> str:
    return (
        f"👋 Hello <b>{name}</b>!\n\n"
        f"🎵 Main <b>{html.escape(BOT_NAME)}</b> hu. Telegram ke voice chat me "
        "high quality music bajata hu.\n\n"
        "➕ Mujhe group me add karo, voice chat start karo aur "
        "/play [gaane ka naam] likho.\n\n"
        "👇 Neeche ke buttons se help ya support lo."
    )


def home_kb(username: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "➕ Mujhe group me add karo",
                url=f"https://t.me/{username}?startgroup=true&admin=manage_video_chats+delete_messages+invite_users",
            )
        ],
        [InlineKeyboardButton("📖 Help", callback_data="help")],
    ]
    if OWNER_USERNAME:
        rows[1].append(InlineKeyboardButton("👑 Owner", url=f"https://t.me/{OWNER_USERNAME}"))
    elif OWNERS:
        rows[1].append(InlineKeyboardButton("👑 Owner", user_id=sorted(OWNERS)[0]))
    row3 = []
    if SUPPORT_LINK:
        row3.append(InlineKeyboardButton("💬 Support", url=SUPPORT_LINK))
    if UPDATES_LINK:
        row3.append(InlineKeyboardButton("📢 Updates", url=UPDATES_LINK))
    if row3:
        rows.append(row3)
    return InlineKeyboardMarkup(rows)


@bot.on_message(filters.command("start"))
async def start_cmd(client, m: Message):
    if m.chat.type == ChatType.PRIVATE and m.from_user:
        add_user(m.from_user.id)
    name = html.escape(m.from_user.first_name) if m.from_user else "dost"
    caption = start_text(name)
    kb = home_kb(client.me.username)
    if START_IMAGE:
        try:
            return await m.reply_photo(START_IMAGE, caption=caption, reply_markup=kb)
        except Exception as e:
            print("START_IMAGE fail (URL/file_id check karo):", repr(e), flush=True)
    await m.reply_text(caption, reply_markup=kb, disable_web_page_preview=True)


async def _edit(q: CallbackQuery, text: str, kb: InlineKeyboardMarkup):
    try:
        if q.message.photo:
            await q.message.edit_caption(text, reply_markup=kb)
        else:
            await q.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except MessageNotModified:
        pass


@bot.on_callback_query(filters.regex("^(help|home)$"))
async def menu_cb(client, q: CallbackQuery):
    if q.data == "help":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home")]])
        await _edit(q, HELP_TEXT, kb)
    else:
        await _edit(q, start_text(html.escape(q.from_user.first_name)), home_kb(client.me.username))
    await q.answer()


# ---------------------------------------------------------------- OWNER COMMANDS
@bot.on_message(filters.command("stats") & filters.user(list(OWNERS)))
async def stats_cmd(_, m: Message):
    up = int(time.time() - START_TIME)
    active = sum(1 for q in queues.values() if q)
    await m.reply_text(
        "📊 <b>Bot Stats</b>\n\n"
        f"👤 Users (DM me start kiya): {len(load_users())}\n"
        f"🎧 Abhi active chats: {active}\n"
        f"⏱ Uptime: {up // 3600}h {(up % 3600) // 60}m\n"
        f"🤖 Helper ready: {'Haan' if calls_ready else 'Nahi'}"
    )


@bot.on_message(filters.command("broadcast") & filters.user(list(OWNERS)))
async def broadcast_cmd(client, m: Message):
    if m.reply_to_message:
        src = m.reply_to_message
    elif len(m.command) > 1:
        src = None
        text = m.text.split(None, 1)[1]
    else:
        return await m.reply_text("Use: /broadcast [message]  ya kisi message ko reply karke /broadcast")
    users = list(load_users())
    ok = fail = 0
    status = await m.reply_text(f"📣 {len(users)} users ko bhej raha hu...")
    for uid in users:
        try:
            if src:
                await src.copy(uid)
            else:
                await client.send_message(uid, text)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Broadcast khatam\nSent: {ok}\nFailed: {fail}")


# Owner bot ko DM me photo bheje to START_IMAGE ke liye file_id mil jayega
@bot.on_message(filters.photo & filters.private & filters.user(list(OWNERS)))
async def get_file_id(_, m: Message):
    await m.reply_text(
        "🖼 Is photo ka file_id (START_IMAGE me daalo):\n\n"
        f"<code>{m.photo.file_id}</code>"
    )


@bot.on_message(
    filters.command(["play", "skip", "pause", "resume", "stop", "queue"]) & filters.private
)
async def private_warn(_, m: Message):
    await m.reply_text("⚠️ Ye command sirf group me chalta hai. Bot ko group me add karke use karo.")


@bot.on_message(filters.command("play") & filters.group)
async def play_cmd(_, m: Message):
    if len(m.command) < 2:
        return await m.reply_text("Use: /play [gaane ka naam ya YouTube link]")
    if not calls_ready:
        return await m.reply_text(
            "❌ Helper ID abhi start nahi hua. Render Logs me ERROR line dekho (SESSION_STRING check karo)."
        )
    chat_id = m.chat.id
    msg = await m.reply_text("🔎 Dhoond raha hu...")
    try:
        track = await asyncio.get_running_loop().run_in_executor(
            None, _extract, m.text.split(None, 1)[1]
        )
    except Exception as e:
        return await msg.edit_text(f"❌ Gaana nahi mila: {str(e)[:300]}")

    queues.setdefault(chat_id, []).append(track)
    if len(queues[chat_id]) == 1:
        try:
            await start_track(chat_id)
            await msg.edit_text(f"▶️ Ab chal raha hai: {track['title']}")
        except Exception as e:
            queues[chat_id].clear()
            await msg.edit_text(
                f"❌ Play nahi hua: {str(e)[:300]}\n\n"
                "Check karo: group me voice chat ON hai, aur helper ID group me hai."
            )
    else:
        await msg.edit_text(f"➕ Queue me add hua (#{len(queues[chat_id]) - 1}): {track['title']}")


@bot.on_message(filters.command("skip") & filters.group)
async def skip_cmd(client, m: Message):
    if not await is_authorized(client, m):
        return await m.reply_text("❌ Ye command sirf group admins ya owner ke liye hai.")
    q = queues.get(m.chat.id)
    if not q:
        return await m.reply_text("Kuch chal hi nahi raha.")
    q.pop(0)
    if q:
        await start_track(m.chat.id)
        await m.reply_text(f"⏭ Ab chal raha hai: {q[0]['title']}")
    else:
        await calls.leave_call(m.chat.id)
        await m.reply_text("Queue khatam. Voice chat chhod diya.")


@bot.on_message(filters.command("pause") & filters.group)
async def pause_cmd(client, m: Message):
    if not await is_authorized(client, m):
        return await m.reply_text("❌ Ye command sirf group admins ya owner ke liye hai.")
    await calls.pause(m.chat.id)
    await m.reply_text("⏸ Paused")


@bot.on_message(filters.command("resume") & filters.group)
async def resume_cmd(client, m: Message):
    if not await is_authorized(client, m):
        return await m.reply_text("❌ Ye command sirf group admins ya owner ke liye hai.")
    await calls.resume(m.chat.id)
    await m.reply_text("▶️ Resumed")


@bot.on_message(filters.command("stop") & filters.group)
async def stop_cmd(client, m: Message):
    if not await is_authorized(client, m):
        return await m.reply_text("❌ Ye command sirf group admins ya owner ke liye hai.")
    queues.pop(m.chat.id, None)
    try:
        await calls.leave_call(m.chat.id)
    except Exception:
        pass
    await m.reply_text("⏹ Band kar diya.")


@bot.on_message(filters.command("queue") & filters.group)
async def queue_cmd(_, m: Message):
    q = queues.get(m.chat.id)
    if not q:
        return await m.reply_text("Queue khali hai.")
    text = f"▶️ {q[0]['title']}\n" + "\n".join(
        f"{i}. {t['title']}" for i, t in enumerate(q[1:], 1)
    )
    await m.reply_text(text)


# Gaana khatam hone par apne aap agla gaana
@calls.on_update(fl.stream_end)
async def on_end(_, update):
    chat_id = update.chat_id
    q = queues.get(chat_id)
    if q:
        q.pop(0)
    if q:
        await start_track(chat_id)
    else:
        queues.pop(chat_id, None)
        await calls.leave_call(chat_id)


# ---------------------------------------------------------------- RENDER KEEP-ALIVE SERVER
class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Music bot is running")

    def log_message(self, *args):
        pass


def start_health_server():
    # Render ke Web Service ko ek open PORT chahiye hota hai
    port = int(os.getenv("PORT", "10000"))
    threading.Thread(
        target=HTTPServer(("0.0.0.0", port), _Health).serve_forever, daemon=True
    ).start()


# ---------------------------------------------------------------- MAIN
async def main():
    global calls_ready
    start_health_server()
    print("STEP 1: health server chalu", flush=True)

    # 1) Pehle sirf bot start karo, taaki commands turant kaam karein
    try:
        await asyncio.wait_for(bot.start(), timeout=90)
        me = await bot.get_me()
        print(f"STEP 2: BOT START OK -> @{me.username}", flush=True)
    except Exception as e:
        print("ERROR: BOT START FAIL (BOT_TOKEN / API_ID / API_HASH check karo):", repr(e), flush=True)
        traceback.print_exc()
        await asyncio.sleep(10**9)   # process zinda rakho taaki logs dikhein

    if os.getenv("TEST_MODE") == "1":
        print("TEST_MODE ON: helper/pytgcalls skip. Bot ko /ping bhejo.", flush=True)
        await pyro_idle()
        return

    # 2) Ab helper ID + voice chat part
    try:
        await asyncio.wait_for(assistant.start(), timeout=90)
        helper = await assistant.get_me()
        print(f"STEP 3: HELPER START OK -> {helper.first_name} ({helper.id})", flush=True)
        await asyncio.wait_for(calls.start(), timeout=90)
        calls_ready = True
        print("STEP 4: PYTGCALLS START OK -> Bot poori tarah ready ✅", flush=True)
    except Exception as e:
        print("ERROR: HELPER / PYTGCALLS START FAIL (SESSION_STRING check karo):", repr(e), flush=True)
        traceback.print_exc()
        print("Bot commands chalenge, par gaana tab tak nahi bajega jab tak ye theek na ho.", flush=True)

    await idle()


if __name__ == "__main__":
    loop.run_until_complete(main())
