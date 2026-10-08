import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List

from pyrogram import Client, filters
from pyrogram.types import Message

from pytgcalls import PyTgCalls
from pytgcalls import filters as tg_filters
from pytgcalls.types import AudioQuality
from pytgcalls.types import MediaStream
from pytgcalls.types import StreamEnded
from pytgcalls.types import VideoQuality

from ytmusicapi import YTMusic

from config import (
    API_HASH,
    API_ID,
    ASSISTANT_SESSION,
    BOT_TOKEN,
    GROUP_ID,
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("MusicBot")


# ============================================================
# TELEGRAM CLIENTS
# ============================================================

bot = Client(
    "music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

assistant = Client(
    "music_assistant",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=ASSISTANT_SESSION,
)


# ============================================================
# PYTGCALLS
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
    duration: str = "Unknown"

    @property
    def youtube_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


queues: Dict[int, List[Song]] = {}
current_song: Dict[int, Song] = {}

play_locks: Dict[int, asyncio.Lock] = {}


# ============================================================
# LOCK
# ============================================================

def get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in play_locks:
        play_locks[chat_id] = asyncio.Lock()

    return play_locks[chat_id]


# ============================================================
# YOUTUBE MUSIC SEARCH
# ============================================================

async def search_song(query: str) -> List[Song]:
    def search():
        return ytmusic.search(
            query,
            filter="songs",
            limit=5,
        )

    results = await asyncio.to_thread(search)

    songs = []

    for item in results:
        video_id = item.get("videoId")

        if not video_id:
            continue

        title = item.get("title", "Unknown")

        artists = item.get("artists", [])

        if artists:
            artist = ", ".join(
                artist_data.get("name", "Unknown")
                for artist_data in artists
            )
        else:
            artist = "Unknown"

        duration = item.get("duration", "Unknown")

        songs.append(
            Song(
                title=title,
                artist=artist,
                video_id=video_id,
                duration=duration,
            )
        )

    return songs


# ============================================================
# CREATE STREAM
# ============================================================

def create_stream(song: Song) -> MediaStream:
    return MediaStream(
        song.youtube_url,
        AudioQuality.HIGH,
        VideoQuality.DEFAULT,
        ytdlp_parameters=(
            "--no-playlist "
            "--extract-audio"
        ),
    )


# ============================================================
# PLAY SONG
# ============================================================

async def play_song(chat_id: int, song: Song):
    async with get_lock(chat_id):

        logger.info(
            "Starting stream in %s: %s - %s",
            chat_id,
            song.title,
            song.artist,
        )

        stream = create_stream(song)

        await voice.play(
            chat_id,
            stream,
        )

        current_song[chat_id] = song

        logger.info(
            "Now playing in %s: %s",
            chat_id,
            song.title,
        )


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(chat_id: int):
    queue = queues.get(chat_id, [])

    if not queue:
        current_song.pop(chat_id, None)

        logger.info(
            "Queue finished in %s",
            chat_id,
        )

        return

    song = queue.pop(0)

    if not queue:
        queues.pop(chat_id, None)

    await play_song(
        chat_id,
        song,
    )


# ============================================================
# STREAM END
# ============================================================

@voice.on_update(
    tg_filters.stream_end()
)
async def stream_ended_handler(
    _,
    update: StreamEnded,
):
    chat_id = update.chat_id

    logger.info(
        "Stream ended in %s",
        chat_id,
    )

    current_song.pop(
        chat_id,
        None,
    )

    await asyncio.sleep(1)

    if queues.get(chat_id):
        await play_next(chat_id)


# ============================================================
# START COMMAND
# ============================================================

@bot.on_message(
    filters.command("start")
)
async def start_handler(
    _,
    message: Message,
):
    await message.reply_text(
        "🎵 **Music Bot Online!**\n\n"
        "Use `/play song name` to play music.\n"
        "Use `/help` for commands."
    )


# ============================================================
# HELP
# ============================================================

@bot.on_message(
    filters.command("help")
)
async def help_handler(
    _,
    message: Message,
):
    await message.reply_text(
        "🎵 **Music Bot Commands**\n\n"
        "/play <song> - Play/search music\n"
        "/queue - Show queue\n"
        "/now - Current song\n"
        "/skip - Skip current song\n"
        "/pause - Pause playback\n"
        "/resume - Resume playback\n"
        "/stop - Stop and leave VC"
    )


# ============================================================
# PLAY
# ============================================================

@bot.on_message(
    filters.command("play")
)
async def play_handler(
    _,
    message: Message,
):
    if len(message.command) < 2:
        await message.reply_text(
            "❌ Use:\n"
            "`/play song name`"
        )

        return

    query = " ".join(
        message.command[1:]
    )

    status = await message.reply_text(
        f"🔎 Searching YouTube Music for:\n"
        f"`{query}`"
    )

    try:
        songs = await search_song(query)

    except Exception as error:
        logger.exception(
            "YouTube Music search failed"
        )

        await status.edit_text(
            f"❌ Search failed:\n`{error}`"
        )

        return

    if not songs:
        await status.edit_text(
            "❌ No songs found."
        )

        return

    song = songs[0]

    chat_id = message.chat.id

    if chat_id in current_song:

        queues.setdefault(
            chat_id,
            [],
        ).append(song)

        position = len(
            queues[chat_id]
        )

        await status.edit_text(
            "➕ **Added to queue**\n\n"
            f"🎵 **{song.title}**\n"
            f"👤 {song.artist}\n"
            f"⏱ {song.duration}\n\n"
            f"📌 Position: `{position}`"
        )

        return

    try:
        await play_song(
            chat_id,
            song,
        )

        await status.edit_text(
            "▶️ **Now Playing**\n\n"
            f"🎵 **{song.title}**\n"
            f"👤 {song.artist}\n"
            f"⏱ {song.duration}"
        )

    except Exception as error:
        logger.exception(
            "Playback failed"
        )

        await status.edit_text(
            f"❌ Playback failed:\n`{error}`"
        )


# ============================================================
# QUEUE
# ============================================================

@bot.on_message(
    filters.command("queue")
)
async def queue_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    queue = queues.get(
        chat_id,
        [],
    )

    if not queue:
        await message.reply_text(
            "📭 Queue is empty."
        )

        return

    text = "🎵 **Queue**\n\n"

    for index, song in enumerate(
        queue,
        start=1,
    ):
        text += (
            f"`{index}.` "
            f"**{song.title}** "
            f"— {song.artist}\n"
        )

    await message.reply_text(
        text
    )


# ============================================================
# NOW PLAYING
# ============================================================

@bot.on_message(
    filters.command("now")
)
async def now_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    song = current_song.get(
        chat_id
    )

    if not song:
        await message.reply_text(
            "⏹ Nothing is playing."
        )

        return

    await message.reply_text(
        "🎵 **Now Playing**\n\n"
        f"🎧 **{song.title}**\n"
        f"👤 {song.artist}\n"
        f"⏱ {song.duration}"
    )


# ============================================================
# SKIP
# ============================================================

@bot.on_message(
    filters.command("skip")
)
async def skip_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "⏹ Nothing is playing."
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

        await asyncio.sleep(1)

        if queues.get(chat_id):
            await play_next(
                chat_id
            )

            song = current_song.get(
                chat_id
            )

            if song:
                await message.reply_text(
                    "⏭ **Skipped**\n\n"
                    f"▶️ Now playing:\n"
                    f"**{song.title}**"
                )

        else:
            await message.reply_text(
                "⏭ Skipped.\n"
                "📭 Queue is empty."
            )

    except Exception as error:
        logger.exception(
            "Skip failed"
        )

        await message.reply_text(
            f"❌ Skip failed:\n`{error}`"
        )


# ============================================================
# PAUSE
# ============================================================

@bot.on_message(
    filters.command("pause")
)
async def pause_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "⏹ Nothing is playing."
        )

        return

    try:
        await voice.pause(
            chat_id
        )

        await message.reply_text(
            "⏸ Playback paused."
        )

    except Exception as error:
        logger.exception(
            "Pause failed"
        )

        await message.reply_text(
            f"❌ Pause failed:\n`{error}`"
        )


# ============================================================
# RESUME
# ============================================================

@bot.on_message(
    filters.command("resume")
)
async def resume_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "⏹ Nothing is playing."
        )

        return

    try:
        await voice.resume(
            chat_id
        )

        await message.reply_text(
            "▶️ Playback resumed."
        )

    except Exception as error:
        logger.exception(
            "Resume failed"
        )

        await message.reply_text(
            f"❌ Resume failed:\n`{error}`"
        )


# ============================================================
# STOP
# ============================================================

@bot.on_message(
    filters.command("stop")
)
async def stop_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    queues.pop(
        chat_id,
        None,
    )

    current_song.pop(
        chat_id,
        None,
    )

    try:
        await voice.leave_call(
            chat_id
        )

    except Exception:
        pass

    await message.reply_text(
        "⏹ **Playback stopped.**\n"
        "📭 Queue cleared."
    )


# ============================================================
# STARTUP LOG MESSAGE
# ============================================================

async def send_startup_message(
    bot_user,
    assistant_user,
):
    text = (
        "🤖 **MUSIC BOT STARTED**\n\n"

        "🤖 **Bot**\n"
        f"├─ ID: `{bot_user.id}`\n"
        f"└─ Username: "
        f"@{bot_user.username or 'N/A'}\n\n"

        "👤 **Assistant**\n"
        f"├─ ID: `{assistant_user.id}`\n"
        f"└─ Username: "
        f"@{assistant_user.username or 'N/A'}\n\n"

        "🟢 **Bot:** Online\n"
        "🟢 **Assistant:** Online\n"
        "🟢 **PyTgCalls:** Ready\n"
        "🎵 **Music System:** Ready"
    )

    await bot.send_message(
        GROUP_ID,
        text,
    )


# ============================================================
# MAIN
# ============================================================

async def main():
    logger.info(
        "Starting Music Bot..."
    )

    # Start Bot
    await bot.start()

    bot_user = await bot.get_me()

    logger.info(
        "Bot started: @%s | ID: %s",
        bot_user.username,
        bot_user.id,
    )

    # Start Assistant
    await assistant.start()

    assistant_user = await assistant.get_me()

    logger.info(
        "Assistant started: @%s | ID: %s",
        assistant_user.username,
        assistant_user.id,
    )

    # Start Voice Client
    voice.start()

    logger.info(
        "PyTgCalls started."
    )

    # Send startup information
    try:
        await send_startup_message(
            bot_user,
            assistant_user,
        )

        logger.info(
            "Startup message sent to GROUP_ID: %s",
            GROUP_ID,
        )

    except Exception as error:
        logger.exception(
            "Could not send startup message: %s",
            error,
        )

    logger.info(
        "======================================"
    )

    logger.info(
        "MUSIC BOT IS READY"
    )

    logger.info(
        "======================================"
    )

    # Keep clients alive
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
        logger.info(
            "Bot stopped."
        )

    except Exception as error:
        logger.exception(
            "Fatal error: %s",
            error,
        )
