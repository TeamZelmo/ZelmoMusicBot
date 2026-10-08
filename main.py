import asyncio
import logging
from dataclasses import dataclass

from pyrogram import Client, filters
from pyrogram.types import Message

from pytgcalls import PyTgCalls
from pytgcalls.types import AudioQuality, MediaStream, VideoQuality
from ytmusicapi import YTMusic

from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    ASSISTANT_SESSION,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


# =========================
# Telegram Clients
# =========================

bot = Client(
    "music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

assistant = Client(
    "assistant",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=ASSISTANT_SESSION,
)


# =========================
# PyTgCalls
# =========================

voice = PyTgCalls(assistant)


# =========================
# YouTube Music
# =========================

ytmusic = YTMusic()


# =========================
# Data
# =========================

@dataclass
class Song:
    title: str
    artist: str
    video_id: str
    duration: int = 0

    @property
    def url(self):
        return f"https://www.youtube.com/watch?v={self.video_id}"


queues = {}
current_song = {}


# =========================
# Helpers
# =========================

async def search_song(query: str):
    try:
        results = await asyncio.to_thread(
            ytmusic.search,
            query,
            filter="songs",
            limit=5,
        )

        if not results:
            return None

        for result in results:
            video_id = result.get("videoId")

            if not video_id:
                continue

            title = result.get("title", "Unknown")

            artists = result.get("artists", [])
            if artists:
                artist = ", ".join(
                    artist.get("name", "Unknown")
                    for artist in artists
                )
            else:
                artist = "Unknown"

            duration_seconds = 0

            duration_text = result.get("duration")

            if duration_text:
                try:
                    parts = duration_text.split(":")

                    if len(parts) == 2:
                        minutes, seconds = map(int, parts)
                        duration_seconds = minutes * 60 + seconds

                    elif len(parts) == 3:
                        hours, minutes, seconds = map(int, parts)
                        duration_seconds = (
                            hours * 3600
                            + minutes * 60
                            + seconds
                        )

                except Exception:
                    duration_seconds = 0

            return Song(
                title=title,
                artist=artist,
                video_id=video_id,
                duration=duration_seconds,
            )

    except Exception as e:
        logger.exception("YouTube Music search failed: %s", e)

    return None


def format_song(song: Song):
    return (
        f"🎵 **{song.title}**\n"
        f"👤 **Artist:** {song.artist}"
    )


async def play_song(chat_id: int, song: Song):
    stream = MediaStream(
        song.url,
        AudioQuality.HIGH,
        VideoQuality.HD_720p,
        ytdlp_parameters=(
            "--format bestaudio/best "
            "--no-playlist "
            "--no-warnings "
            "--quiet"
        ),
    )

    await voice.play(
        chat_id,
        stream,
    )

    current_song[chat_id] = song


async def play_next(chat_id: int):
    queue = queues.get(chat_id, [])

    if not queue:
        current_song.pop(chat_id, None)
        return False

    song = queue.pop(0)

    queues[chat_id] = queue

    await play_song(chat_id, song)

    return True


# =========================
# /start
# =========================

@bot.on_message(filters.command("start"))
async def start_command(_, message: Message):
    await message.reply_text(
        "🎵 **Music Bot**\n\n"
        "I can play YouTube Music audio in Telegram Voice Chats.\n\n"
        "Use `/help` to see all commands."
    )


# =========================
# /help
# =========================

@bot.on_message(filters.command("help"))
async def help_command(_, message: Message):
    await message.reply_text(
        "🎵 **Music Bot Commands**\n\n"
        "▶️ `/play <song>` - Play a song\n"
        "📋 `/queue` - Show queue\n"
        "🎵 `/now` - Current song\n"
        "⏭ `/skip` - Skip current song\n"
        "⏸ `/pause` - Pause playback\n"
        "▶️ `/resume` - Resume playback\n"
        "⏹ `/stop` - Stop playback\n"
        "❓ `/help` - Show this help"
    )


# =========================
# /play
# =========================

@bot.on_message(filters.command("play"))
async def play_command(_, message: Message):
    if len(message.command) < 2:
        await message.reply_text(
            "❌ Usage:\n"
            "`/play song name`"
        )
        return

    query = " ".join(message.command[1:])

    status = await message.reply_text(
        f"🔎 Searching for **{query}**..."
    )

    song = await search_song(query)

    if not song:
        await status.edit_text(
            "❌ Song not found."
        )
        return

    chat_id = message.chat.id

    try:
        # Check whether a call is already active.
        if chat_id in current_song:
            queues.setdefault(chat_id, []).append(song)

            position = len(queues[chat_id])

            await status.edit_text(
                f"➕ **Added to queue**\n\n"
                f"{format_song(song)}\n\n"
                f"📍 Position: `{position}`"
            )

            return

        await play_song(chat_id, song)

        await status.edit_text(
            f"▶️ **Now Playing**\n\n"
            f"{format_song(song)}"
        )

    except Exception as e:
        logger.exception("Playback error: %s", e)

        await status.edit_text(
            "❌ Could not start playback.\n\n"
            "Make sure the Assistant account is in the group "
            "and the Telegram Voice Chat is active."
        )


# =========================
# /queue
# =========================

@bot.on_message(filters.command("queue"))
async def queue_command(_, message: Message):
    chat_id = message.chat.id

    queue = queues.get(chat_id, [])

    if not queue:
        await message.reply_text(
            "📋 Queue is empty."
        )
        return

    text = "📋 **Music Queue**\n\n"

    for index, song in enumerate(queue, start=1):
        text += (
            f"`{index}.` **{song.title}**\n"
            f"👤 {song.artist}\n\n"
        )

    await message.reply_text(text)


# =========================
# /now
# =========================

@bot.on_message(filters.command("now"))
async def now_command(_, message: Message):
    chat_id = message.chat.id

    song = current_song.get(chat_id)

    if not song:
        await message.reply_text(
            "❌ Nothing is playing."
        )
        return

    await message.reply_text(
        f"🎵 **Now Playing**\n\n"
        f"{format_song(song)}"
    )


# =========================
# /skip
# =========================

@bot.on_message(filters.command("skip"))
async def skip_command(_, message: Message):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "❌ Nothing is playing."
        )
        return

    try:
        await voice.leave_call(chat_id)

        current_song.pop(chat_id, None)

        played = await play_next(chat_id)

        if played:
            song = current_song[chat_id]

            await message.reply_text(
                f"⏭ **Skipped**\n\n"
                f"▶️ Now Playing:\n"
                f"{format_song(song)}"
            )
        else:
            await message.reply_text(
                "⏭ Song skipped.\n"
                "📭 Queue is empty."
            )

    except Exception as e:
        logger.exception("Skip error: %s", e)

        await message.reply_text(
            "❌ Could not skip the song."
        )


# =========================
# /pause
# =========================

@bot.on_message(filters.command("pause"))
async def pause_command(_, message: Message):
    chat_id = message.chat.id

    try:
        await voice.pause(chat_id)

        await message.reply_text(
            "⏸ Playback paused."
        )

    except Exception as e:
        logger.exception("Pause error: %s", e)

        await message.reply_text(
            "❌ Nothing is playing or playback cannot be paused."
        )


# =========================
# /resume
# =========================

@bot.on_message(filters.command("resume"))
async def resume_command(_, message: Message):
    chat_id = message.chat.id

    try:
        await voice.resume(chat_id)

        await message.reply_text(
            "▶️ Playback resumed."
        )

    except Exception as e:
        logger.exception("Resume error: %s", e)

        await message.reply_text(
            "❌ Nothing is paused."
        )


# =========================
# /stop
# =========================

@bot.on_message(filters.command("stop"))
async def stop_command(_, message: Message):
    chat_id = message.chat.id

    try:
        await voice.leave_call(chat_id)

        queues.pop(chat_id, None)
        current_song.pop(chat_id, None)

        await message.reply_text(
            "⏹ **Playback stopped.**\n"
            "🗑 Queue cleared."
        )

    except Exception as e:
        logger.exception("Stop error: %s", e)

        queues.pop(chat_id, None)
        current_song.pop(chat_id, None)

        await message.reply_text(
            "🗑 Queue cleared."
        )


# =========================
# Main
# =========================

async def main():
    await bot.start()
    await assistant.start()
    await voice.start()

    me = await bot.get_me()
    assistant_me = await assistant.get_me()

    logger.info(
        "Bot started: @%s",
        me.username,
    )

    logger.info(
        "Assistant started: @%s",
        assistant_me.username or assistant_me.first_name,
    )

    print()
    print("====================================")
    print("       TELEGRAM MUSIC BOT")
    print("====================================")
    print(f"Bot: @{me.username}")
    print(
        f"Assistant: "
        f"@{assistant_me.username or assistant_me.first_name}"
    )
    print("Status: ONLINE")
    print("====================================")
    print()

    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\nBot stopped.")
