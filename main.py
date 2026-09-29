import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from pyrogram import Client, filters, idle as pyro_idle
from pyrogram.handlers import RawUpdateHandler
from pyrogram.types import Message
from pytgcalls import PyTgCalls, idle, filters as fl
from pytgcalls.types import MediaStream
import yt_dlp

import logging
logging.basicConfig(level=logging.INFO)

load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION_STRING = os.getenv("SESSION_STRING")

# Bot: commands sunta hai | Assistant: voice chat me gaana bajata hai
bot = Client("musicbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
assistant = Client("assistant", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
calls = PyTgCalls(assistant)

queues: dict[int, list[dict]] = {}   # chat_id -> [{title, url}, ...]

YDL_OPTS = {"format": "bestaudio/best", "quiet": True, "noplaylist": True}


def _extract(query: str) -> dict:
    if not query.startswith("http"):
        query = f"ytsearch1:{query}"
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return {"title": info["title"], "url": info["url"]}


async def start_track(chat_id: int) -> bool:
    q = queues.get(chat_id)
    if not q:
        return False
    await calls.play(chat_id, MediaStream(q[0]["url"]))
    return True


@bot.on_message(group=-1)
async def _debug_log(_, m: Message):
    print(f"[MSG] chat={m.chat.id} type={m.chat.type} text={m.text}", flush=True)


@bot.on_message(filters.command("ping"))
async def ping_cmd(_, m: Message):
    await m.reply_text("🏓 Pong! Bot zinda hai.")


@bot.on_message(
    filters.command(["play", "skip", "pause", "resume", "stop", "queue"]) & filters.private
)
async def private_warn(_, m: Message):
    await m.reply_text("⚠️ Ye command sirf group me chalta hai. Bot ko group me add karke use karo.")


@bot.on_message(filters.command("start"))
async def start_cmd(_, m: Message):
    await m.reply_text(
        "🎵 Music Bot\n\n"
        "/play <naam ya link> - gaana bajao\n"
        "/skip - agla gaana\n"
        "/pause - roko\n"
        "/resume - dobara chalao\n"
        "/queue - queue dekho\n"
        "/stop - band karo"
    )


@bot.on_message(filters.command("play") & filters.group)
async def play_cmd(_, m: Message):
    if len(m.command) < 2:
        return await m.reply_text("Use: /play <song name ya YouTube link>")
    chat_id = m.chat.id
    if not calls_ready:
        return await m.reply_text("❌ Helper ID abhi start nahi hua. Render Logs me ERROR line dekho (SESSION_STRING check karo).")
    msg = await m.reply_text("🔎 Dhoond raha hu...")
    try:
        track = await asyncio.get_running_loop().run_in_executor(
            None, _extract, m.text.split(None, 1)[1]
        )
    except Exception as e:
        return await msg.edit_text(f"❌ Gaana nahi mila: {e}")

    queues.setdefault(chat_id, []).append(track)
    if len(queues[chat_id]) == 1:
        try:
            await start_track(chat_id)
            await msg.edit_text(f"▶️ Ab chal raha hai: {track['title']}")
        except Exception as e:
            queues[chat_id].clear()
            await msg.edit_text(
                f"❌ Play nahi hua: {e}\n\n"
                "Check karo: group me voice chat ON hai, aur helper ID group me hai."
            )
    else:
        await msg.edit_text(f"➕ Queue me add hua (#{len(queues[chat_id]) - 1}): {track['title']}")


@bot.on_message(filters.command("skip") & filters.group)
async def skip_cmd(_, m: Message):
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
async def pause_cmd(_, m: Message):
    await calls.pause(m.chat.id)
    await m.reply_text("⏸ Paused")


@bot.on_message(filters.command("resume") & filters.group)
async def resume_cmd(_, m: Message):
    await calls.resume(m.chat.id)
    await m.reply_text("▶️ Resumed")


@bot.on_message(filters.command("stop") & filters.group)
async def stop_cmd(_, m: Message):
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
    text = f"▶️ {q[0]['title']}\n" + "\n".join(f"{i}. {t['title']}" for i, t in enumerate(q[1:], 1))
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


async def _raw_log(client, update, users, chats):
    print(f"[RAW] update aaya: {type(update).__name__}", flush=True)


calls_ready = False


async def main():
    global calls_ready
    start_health_server()
    print("STEP 1: health server chalu", flush=True)

    # 1) Pehle sirf bot start karo, taaki commands turant kaam karein
    try:
        await asyncio.wait_for(bot.start(), timeout=90)
        me = await bot.get_me()
        print(f"STEP 2: BOT START OK -> @{me.username}", flush=True)
        bot.add_handler(RawUpdateHandler(_raw_log), group=-2)
    except Exception as e:
        import traceback
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
        import traceback
        print("ERROR: HELPER / PYTGCALLS START FAIL (SESSION_STRING check karo):", repr(e), flush=True)
        traceback.print_exc()
        print("Bot commands chalenge, par gaana tab tak nahi bajega jab tak ye theek na ho.", flush=True)

    await idle()


if __name__ == "__main__":
    asyncio.run(main())
