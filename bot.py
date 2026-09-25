"""
Telegram Voice Chat Music Bot
- Song search: ytmusicapi (https://github.com/sigma67/ytmusicapi)
- VC streaming: py-tgcalls + yt-dlp

Chalane se pehle:
1. pip install -r requirements.txt
2. python generate_session.py   -> SESSION_STRING milega
3. .env.example ko .env me rename karke saari values bharo
4. python bot.py
"""

import asyncio
import logging
import time

from pyrogram import Client, filters
from pyrogram.types import Message
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, Update
from pytgcalls.types.stream import StreamEnded
from pytgcalls.exceptions import NoActiveGroupCall, NotInCallError

from config import API_ID, API_HASH, BOT_TOKEN, SESSION_STRING, SUDO_USERS
from music import search_track, get_stream_url, get_lyrics
from queue_manager import queue_manager

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("music-bot")

# Bot client -> commands sunta hai
bot = Client("music_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Assistant (userbot) client -> ye actually VC join karke stream karta hai
assistant = Client("assistant", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

call_py = PyTgCalls(assistant)

START_TIME = time.time()


# ----------------------------- Helpers -----------------------------

async def is_admin_or_sudo(message: Message) -> bool:
    if message.from_user and message.from_user.id in SUDO_USERS:
        return True
    member = await message.chat.get_member(message.from_user.id)
    return member.status in ("administrator", "creator")


async def start_stream(chat_id: int, stream_url: str):
    """VC me stream shuru karta hai; agar VC start nahi hai to sahi error dikhata hai."""
    try:
        await call_py.play(chat_id, MediaStream(stream_url))
    except NoActiveGroupCall:
        raise RuntimeError(
            "❌ Group me Voice Chat start nahi hai. Pehle VC start karo, phir /play use karo."
        )
    except Exception as e:
        raise RuntimeError(f"❌ Stream start karne me error: `{e}`")


async def play_next(chat_id: int):
    """Queue me se agla song uthata hai aur VC me play karta hai. Loop on ho to wahi repeat karta hai."""
    cq = queue_manager.get(chat_id)

    if cq.loop and cq.current:
        stream_url = await get_stream_url(cq.current.video_id)
        if stream_url:
            try:
                await start_stream(chat_id, stream_url)
            except Exception as e:
                log.warning(e)
            return

    next_track = queue_manager.pop_next(chat_id)

    if next_track is None:
        cq.current = None
        cq.is_playing = False
        cq.is_paused = False
        try:
            await call_py.leave_call(chat_id)
        except Exception:
            pass
        return

    stream_url = await get_stream_url(next_track.video_id)
    if not stream_url:
        await play_next(chat_id)  # is track ko skip karke agla try karo
        return

    cq.current = next_track
    cq.is_playing = True
    cq.is_paused = False

    try:
        await start_stream(chat_id, stream_url)
    except Exception as e:
        log.warning(e)
        await play_next(chat_id)


# ----------------------------- Commands -----------------------------

@bot.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    await message.reply_text(
        "Namaste! Main ek **Telegram VC Music Bot** hoon 🎶\n\n"
        "Saare commands dekhne ke liye `/help` bhejo."
    )


@bot.on_message(filters.command("help"))
async def help_cmd(_, message: Message):
    text = (
        "🎧 **Music Bot Commands**\n\n"
        "**Playback**\n"
        "`/play <naam>` - gaana search karke play/queue karta hai\n"
        "`/playnext <naam>` - gaana search karke queue ke top pe daalta hai\n"
        "`/pause` - pause\n"
        "`/resume` - resume\n"
        "`/skip` - agla gaana\n"
        "`/stop` ya `/end` - rok do aur queue saaf karo\n"
        "`/replay` - current gaana shuru se dobara\n\n"
        "**Queue**\n"
        "`/queue` ya `/q` - poori queue dikhata hai\n"
        "`/nowplaying` ya `/current` - abhi kya chal raha hai\n"
        "`/shuffle` - queue shuffle karo\n"
        "`/loop` - current gaana repeat on/off\n\n"
        "**Volume**\n"
        "`/volume <0-200>` - awaaz set karo\n"
        "`/mute` - mute karo\n"
        "`/unmute` - unmute karo\n\n"
        "**Extra**\n"
        "`/lyrics` - current gaane ke lyrics\n"
        "`/ping` - bot ka response time + uptime\n"
    )
    await message.reply_text(text)


@bot.on_message(filters.command("ping"))
async def ping_cmd(_, message: Message):
    start = time.time()
    m = await message.reply_text("Pinging...")
    ms = (time.time() - start) * 1000
    uptime = int(time.time() - START_TIME)
    h, rem = divmod(uptime, 3600)
    mm, ss = divmod(rem, 60)
    await m.edit_text(f"🏓 Pong! `{ms:.0f}ms`\n⏱ Uptime: `{h}h {mm}m {ss}s`")


@bot.on_message(filters.command(["play", "playnext"]) & filters.group)
async def play_cmd(_, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: `/play <song naam>`")
        return

    query = message.text.split(None, 1)[1]
    chat_id = message.chat.id
    is_playnext = message.command[0].lower() == "playnext"

    status = await message.reply_text(f"🔎 Search kar raha hoon: **{query}**...")

    track = await search_track(query)
    if not track:
        await status.edit_text("❌ Gaana nahi mila. Kuch aur try karo.")
        return

    cq = queue_manager.get(chat_id)

    if cq.is_playing:
        if is_playnext:
            queue_manager.add_top(chat_id, track)
            await status.edit_text(
                f"⏫ Queue ke top pe daal diya:\n**{track.title}** — {track.artist} [{track.duration}]"
            )
        else:
            queue_manager.add(chat_id, track)
            await status.edit_text(
                f"➕ Queue me add ho gaya:\n**{track.title}** — {track.artist} [{track.duration}]"
            )
        return

    # Kuch bhi nahi chal raha -> seedha VC join karke play karo
    stream_url = await get_stream_url(track.video_id)
    if not stream_url:
        await status.edit_text("❌ Stream URL nahi mil paaya, try again.")
        return

    try:
        cq.current = track
        cq.is_playing = True
        cq.is_paused = False
        await start_stream(chat_id, stream_url)
        await status.edit_text(
            f"▶️ Ab baj raha hai:\n**{track.title}** — {track.artist} [{track.duration}]"
        )
    except Exception as e:
        cq.is_playing = False
        log.exception(e)
        await status.edit_text(
            f"{e}\n\nCheck karo:\n"
            "1. Assistant account is group me hai\n"
            "2. Bot ke paas admin rights hain\n"
            "3. Group me VC start hai"
        )


@bot.on_message(filters.command("pause") & filters.group)
async def pause_cmd(_, message: Message):
    chat_id = message.chat.id
    cq = queue_manager.get(chat_id)
    try:
        await call_py.pause(chat_id)
        cq.is_paused = True
        await message.reply_text("⏸ Paused.")
    except NotInCallError:
        await message.reply_text("❌ Abhi koi VC active nahi hai.")
    except Exception as e:
        await message.reply_text(f"Error: `{e}`")


@bot.on_message(filters.command("resume") & filters.group)
async def resume_cmd(_, message: Message):
    chat_id = message.chat.id
    cq = queue_manager.get(chat_id)
    try:
        await call_py.resume(chat_id)
        cq.is_paused = False
        await message.reply_text("▶️ Resumed.")
    except NotInCallError:
        await message.reply_text("❌ Abhi koi VC active nahi hai.")
    except Exception as e:
        await message.reply_text(f"Error: `{e}`")


@bot.on_message(filters.command("skip") & filters.group)
async def skip_cmd(_, message: Message):
    chat_id = message.chat.id
    cq = queue_manager.get(chat_id)
    if not cq.is_playing:
        await message.reply_text("❌ Abhi kuch bhi play nahi ho raha.")
        return
    await message.reply_text("⏭ Skip kar raha hoon...")
    await play_next(chat_id)


@bot.on_message(filters.command(["stop", "end"]) & filters.group)
async def stop_cmd(_, message: Message):
    chat_id = message.chat.id
    queue_manager.clear(chat_id)
    try:
        await call_py.leave_call(chat_id)
    except Exception:
        pass
    await message.reply_text("⏹ Stop kar diya aur queue saaf kar di.")


@bot.on_message(filters.command("replay") & filters.group)
async def replay_cmd(_, message: Message):
    chat_id = message.chat.id
    cq = queue_manager.get(chat_id)
    if not cq.current:
        await message.reply_text("❌ Koi current gaana nahi hai.")
        return
    stream_url = await get_stream_url(cq.current.video_id)
    if not stream_url:
        await message.reply_text("❌ Stream URL nahi mila.")
        return
    try:
        await start_stream(chat_id, stream_url)
        await message.reply_text(f"🔁 Replay: **{cq.current.title}**")
    except Exception as e:
        await message.reply_text(str(e))


@bot.on_message(filters.command(["queue", "q"]) & filters.group)
async def queue_cmd(_, message: Message):
    cq = queue_manager.get(message.chat.id)
    if not cq.current:
        await message.reply_text("Abhi kuch bhi queue me nahi hai.")
        return

    text = f"🎧 **Ab baj raha hai:** {cq.current.title} — {cq.current.artist}\n\n"
    if cq.queue:
        text += "**Queue:**\n"
        for i, t in enumerate(cq.queue, start=1):
            text += f"{i}. {t.title} — {t.artist}\n"
    else:
        text += "Queue khaali hai."

    await message.reply_text(text)


@bot.on_message(filters.command(["nowplaying", "current"]) & filters.group)
async def nowplaying_cmd(_, message: Message):
    cq = queue_manager.get(message.chat.id)
    if not cq.current:
        await message.reply_text("❌ Kuch bhi play nahi ho raha.")
        return
    status = "⏸ Paused" if cq.is_paused else "▶️ Playing"
    loop_status = "🔁 On" if cq.loop else "🔁 Off"
    await message.reply_text(
        f"{status}\n**{cq.current.title}** — {cq.current.artist}\n"
        f"Duration: {cq.current.duration}\nLoop: {loop_status} | Volume: {cq.volume}%"
    )


@bot.on_message(filters.command("shuffle") & filters.group)
async def shuffle_cmd(_, message: Message):
    chat_id = message.chat.id
    if not queue_manager.get(chat_id).queue:
        await message.reply_text("❌ Queue khaali hai, shuffle karne ko kuch nahi.")
        return
    queue_manager.shuffle(chat_id)
    await message.reply_text("🔀 Queue shuffle kar di.")


@bot.on_message(filters.command("loop") & filters.group)
async def loop_cmd(_, message: Message):
    cq = queue_manager.get(message.chat.id)
    cq.loop = not cq.loop
    await message.reply_text(f"🔁 Loop {'ON' if cq.loop else 'OFF'} kar diya.")


@bot.on_message(filters.command("volume") & filters.group)
async def volume_cmd(_, message: Message):
    chat_id = message.chat.id
    cq = queue_manager.get(chat_id)

    if len(message.command) < 2 or not message.command[1].isdigit():
        await message.reply_text("Usage: `/volume <0-200>`")
        return

    vol = int(message.command[1])
    if vol < 0 or vol > 200:
        await message.reply_text("❌ Volume 0 se 200 ke beech hona chahiye.")
        return

    try:
        await call_py.change_volume_call(chat_id, vol)
        cq.volume = vol
        await message.reply_text(f"🔊 Volume set to {vol}%")
    except NotInCallError:
        await message.reply_text("❌ Abhi koi VC active nahi hai.")
    except Exception as e:
        await message.reply_text(f"Error: `{e}`")


@bot.on_message(filters.command("mute") & filters.group)
async def mute_cmd(_, message: Message):
    chat_id = message.chat.id
    cq = queue_manager.get(chat_id)
    try:
        await call_py.mute(chat_id)
        cq.is_muted = True
        await message.reply_text("🔇 Muted.")
    except Exception as e:
        await message.reply_text(f"Error: `{e}`")


@bot.on_message(filters.command("unmute") & filters.group)
async def unmute_cmd(_, message: Message):
    chat_id = message.chat.id
    cq = queue_manager.get(chat_id)
    try:
        await call_py.unmute(chat_id)
        cq.is_muted = False
        await message.reply_text("🔊 Unmuted.")
    except Exception as e:
        await message.reply_text(f"Error: `{e}`")


@bot.on_message(filters.command("lyrics") & filters.group)
async def lyrics_cmd(_, message: Message):
    cq = queue_manager.get(message.chat.id)
    if not cq.current:
        await message.reply_text("❌ Koi current gaana nahi hai.")
        return
    status = await message.reply_text("🔎 Lyrics dhoondh raha hoon...")
    lyrics = await get_lyrics(cq.current.video_id)
    if not lyrics:
        await status.edit_text("❌ Is gaane ke lyrics nahi mile.")
        return
    if len(lyrics) > 4000:
        lyrics = lyrics[:4000] + "\n\n... (lyrics kaat diye, bahut lambe the)"
    await status.edit_text(f"📝 **{cq.current.title}**\n\n{lyrics}")


# ----------------------------- Stream events -----------------------------

@call_py.on_update()
async def on_stream_update(_, update: Update):
    """Jab current stream khatam ho jaye to queue se agla song play karo."""
    if isinstance(update, StreamEnded):
        chat_id = update.chat_id
        await play_next(chat_id)


# ----------------------------- Main -----------------------------

async def main():
    await bot.start()
    await assistant.start()
    await call_py.start()
    log.info("Bot, assistant aur PyTgCalls sab start ho gaye ✅")
    await asyncio.Event().wait()  # hamesha chalta rahega


if __name__ == "__main__":
    asyncio.run(main())
