import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from pyrogram import Client, filters
from pyrogram.types import Message

from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

from ytmusicapi import YTMusic

from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    ASSISTANT_SESSION,
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("TelegramMusicBot")


# ============================================================
# CLIENTS
# ============================================================

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


# ============================================================
# VOICE CALL
# ============================================================

voice = PyTgCalls(assistant)


# ============================================================
# YOUTUBE MUSIC
# ============================================================

ytmusic = YTMusic()


# ============================================================
# DATA
# ============================================================

@dataclass
class Song:

    title: str
    artist: str
    video_id: str
    duration: int = 0

    @property
    def url(self) -> str:

        return (
            "https://www.youtube.com/watch?v="
            + self.video_id
        )


queues: Dict[int, List[Song]] = {}

current_song: Dict[int, Song] = {}


# ============================================================
# SEARCH
# ============================================================

async def search_song(
    query: str,
) -> Optional[Song]:

    try:

        results = await asyncio.to_thread(
            ytmusic.search,
            query,
            filter="songs",
            limit=5,
        )

    except Exception as error:

        logger.exception(
            "YouTube Music search failed: %s",
            error,
        )

        return None

    if not results:
        return None

    for result in results:

        video_id = result.get(
            "videoId"
        )

        if not video_id:
            continue

        title = result.get(
            "title",
            "Unknown",
        )

        artists = result.get(
            "artists",
            [],
        )

        if artists:

            artist = ", ".join(
                artist.get(
                    "name",
                    "Unknown",
                )
                for artist in artists
            )

        else:

            artist = "Unknown"

        duration = 0

        duration_text = result.get(
            "duration"
        )

        if duration_text:

            try:

                parts = duration_text.split(":")

                if len(parts) == 2:

                    minutes = int(parts[0])
                    seconds = int(parts[1])

                    duration = (
                        minutes * 60
                        + seconds
                    )

                elif len(parts) == 3:

                    hours = int(parts[0])
                    minutes = int(parts[1])
                    seconds = int(parts[2])

                    duration = (
                        hours * 3600
                        + minutes * 60
                        + seconds
                    )

            except Exception:

                duration = 0

        return Song(
            title=title,
            artist=artist,
            video_id=video_id,
            duration=duration,
        )

    return None


# ============================================================
# FORMAT SONG
# ============================================================

def song_text(song: Song) -> str:

    return (
        f"🎵 **{song.title}**\n"
        f"👤 **Artist:** {song.artist}"
    )


# ============================================================
# CREATE MEDIA STREAM
# ============================================================

def create_stream(
    song: Song,
) -> MediaStream:

    return MediaStream(
        song.url,
    )


# ============================================================
# PLAY
# ============================================================

async def play_song(
    chat_id: int,
    song: Song,
):

    stream = create_stream(
        song
    )

    await voice.play(
        chat_id,
        stream,
    )

    current_song[chat_id] = song


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(
    chat_id: int,
) -> bool:

    queue = queues.get(
        chat_id,
        [],
    )

    if not queue:

        current_song.pop(
            chat_id,
            None,
        )

        return False

    song = queue.pop(0)

    queues[chat_id] = queue

    await play_song(
        chat_id,
        song,
    )

    return True


# ============================================================
# START
# ============================================================

@bot.on_message(
    filters.command("start")
)
async def start_command(
    client,
    message: Message,
):

    await message.reply_text(
        "🎵 **Telegram Music Bot**\n\n"
        "I can play music in Telegram Voice Chat.\n\n"
        "Use `/help` for commands."
    )


# ============================================================
# HELP
# ============================================================

@bot.on_message(
    filters.command("help")
)
async def help_command(
    client,
    message: Message,
):

    await message.reply_text(
        "🎵 **Music Commands**\n\n"

        "▶️ `/play <song>`\n"
        "Play a song\n\n"

        "📋 `/queue`\n"
        "Show queue\n\n"

        "🎵 `/now`\n"
        "Show current song\n\n"

        "⏭ `/skip`\n"
        "Skip song\n\n"

        "⏸ `/pause`\n"
        "Pause\n\n"

        "▶️ `/resume`\n"
        "Resume\n\n"

        "⏹ `/stop`\n"
        "Stop and clear queue\n\n"

        "❓ `/help`\n"
        "Show commands"
    )


# ============================================================
# PLAY COMMAND
# ============================================================

@bot.on_message(
    filters.command("play")
)
async def play_command(
    client,
    message: Message,
):

    if len(message.command) < 2:

        await message.reply_text(
            "❌ Please enter a song name.\n\n"
            "Example:\n"
            "`/play Believer Imagine Dragons`"
        )

        return

    query = " ".join(
        message.command[1:]
    )

    status = await message.reply_text(
        f"🔎 Searching for:\n"
        f"**{query}**"
    )

    song = await search_song(
        query
    )

    if not song:

        await status.edit_text(
            "❌ Song not found."
        )

        return

    chat_id = message.chat.id

    # --------------------------------------------------------
    # Add to queue if something is already playing
    # --------------------------------------------------------

    if chat_id in current_song:

        queues.setdefault(
            chat_id,
            [],
        ).append(song)

        position = len(
            queues[chat_id]
        )

        await status.edit_text(
            "➕ **Added to Queue**\n\n"
            f"{song_text(song)}\n\n"
            f"📍 Position: `{position}`"
        )

        return

    # --------------------------------------------------------
    # Start playback
    # --------------------------------------------------------

    try:

        await play_song(
            chat_id,
            song,
        )

        await status.edit_text(
            "▶️ **Now Playing**\n\n"
            f"{song_text(song)}"
        )

    except Exception as error:

        logger.exception(
            "Playback failed: %s",
            error,
        )

        await status.edit_text(
            "❌ **Playback failed.**\n\n"
            "Make sure the Assistant account:\n"
            "• is inside this group\n"
            "• can join the Voice Chat\n"
            "• has permission to speak"
        )


# ============================================================
# QUEUE
# ============================================================

@bot.on_message(
    filters.command("queue")
)
async def queue_command(
    client,
    message: Message,
):

    chat_id = message.chat.id

    queue = queues.get(
        chat_id,
        [],
    )

    if not queue:

        await message.reply_text(
            "📭 **Queue is empty.**"
        )

        return

    text = "📋 **Music Queue**\n\n"

    for index, song in enumerate(
        queue,
        start=1,
    ):

        text += (
            f"`{index}.` **{song.title}**\n"
            f"👤 {song.artist}\n\n"
        )

    await message.reply_text(
        text
    )


# ============================================================
# NOW
# ============================================================

@bot.on_message(
    filters.command("now")
)
async def now_command(
    client,
    message: Message,
):

    chat_id = message.chat.id

    song = current_song.get(
        chat_id
    )

    if not song:

        await message.reply_text(
            "❌ Nothing is playing."
        )

        return

    await message.reply_text(
        "🎵 **Now Playing**\n\n"
        f"{song_text(song)}"
    )


# ============================================================
# SKIP
# ============================================================

@bot.on_message(
    filters.command("skip")
)
async def skip_command(
    client,
    message: Message,
):

    chat_id = message.chat.id

    if chat_id not in current_song:

        await message.reply_text(
            "❌ Nothing is playing."
        )

        return

    try:

        await voice.leave_call(
            chat_id
        )

        current_song.pop(
            chat_id,
            None,
        )

        played = await play_next(
            chat_id
        )

        if played:

            song = current_song[
                chat_id
            ]

            await message.reply_text(
                "⏭ **Skipped**\n\n"
                "▶️ **Now Playing**\n"
                f"{song_text(song)}"
            )

        else:

            await message.reply_text(
                "⏭ Skipped.\n"
                "📭 Queue is empty."
            )

    except Exception as error:

        logger.exception(
            "Skip failed: %s",
            error,
        )

        await message.reply_text(
            "❌ Could not skip the song."
        )


# ============================================================
# PAUSE
# ============================================================

@bot.on_message(
    filters.command("pause")
)
async def pause_command(
    client,
    message: Message,
):

    chat_id = message.chat.id

    try:

        await voice.pause(
            chat_id
        )

        await message.reply_text(
            "⏸ **Paused.**"
        )

    except Exception as error:

        logger.exception(
            "Pause failed: %s",
            error,
        )

        await message.reply_text(
            "❌ Could not pause playback."
        )


# ============================================================
# RESUME
# ============================================================

@bot.on_message(
    filters.command("resume")
)
async def resume_command(
    client,
    message: Message,
):

    chat_id = message.chat.id

    try:

        await voice.resume(
            chat_id
        )

        await message.reply_text(
            "▶️ **Resumed.**"
        )

    except Exception as error:

        logger.exception(
            "Resume failed: %s",
            error,
        )

        await message.reply_text(
            "❌ Could not resume playback."
        )


# ============================================================
# STOP
# ============================================================

@bot.on_message(
    filters.command("stop")
)
async def stop_command(
    client,
    message: Message,
):

    chat_id = message.chat.id

    try:

        await voice.leave_call(
            chat_id
        )

    except Exception as error:

        logger.warning(
            "Leave call error: %s",
            error,
        )

    queues.pop(
        chat_id,
        None,
    )

    current_song.pop(
        chat_id,
        None,
    )

    await message.reply_text(
        "⏹ **Stopped.**\n"
        "🗑 Queue cleared."
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    print()
    print("========================================")
    print("        TELEGRAM MUSIC BOT")
    print("========================================")

    await bot.start()

    print("✓ Bot started")

    await assistant.start()

    print("✓ Assistant started")

    await voice.start()

    print("✓ Voice system started")

    bot_user = await bot.get_me()

    assistant_user = await assistant.get_me()

    print()
    print(
        "Bot       : "
        f"@{bot_user.username or bot_user.first_name}"
    )

    print(
        "Assistant : "
        f"@{assistant_user.username or assistant_user.first_name}"
    )

    print()
    print("Status    : ONLINE")
    print("========================================")
    print()

    await asyncio.Event().wait()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "Bot stopped."
        )
