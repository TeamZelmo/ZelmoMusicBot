import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List

from pyrogram import Client, filters
from pyrogram.types import Message

from pytgcalls import PyTgCalls
from pytgcalls import filters as tg_filters
from pytgcalls.types import AudioQuality, MediaStream, StreamEnded

from ytmusicapi import YTMusic

from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    ASSISTANT_SESSION,
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
# CLIENTS
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
# PYTGCalls
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

async def search_song(query: str):
    try:
        results = await asyncio.to_thread(
            ytmusic.search,
            query,
            filter="songs",
            limit=5,
        )

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

    except Exception as error:
        logger.exception("YouTube Music search failed: %s", error)
        return []


# ============================================================
# CREATE AUDIO STREAM
# ============================================================

def create_stream(song: Song) -> MediaStream:
    """
    Create an audio-only stream.

    IMPORTANT:
    Do not use VideoQuality.DEFAULT here.
    """

    return MediaStream(
        song.youtube_url,
        AudioQuality.HIGH,
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

        try:
            stream = create_stream(song)

            await voice.play(
                chat_id,
                stream,
            )

            current_song[chat_id] = song

            logger.info(
                "Now playing in %s: %s - %s",
                chat_id,
                song.title,
                song.artist,
            )

        except Exception as error:
            logger.exception(
                "Playback failed in %s: %s",
                chat_id,
                error,
            )

            current_song.pop(chat_id, None)


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(chat_id: int):

    if not queues.get(chat_id):
        current_song.pop(chat_id, None)

        logger.info(
            "Queue empty for chat %s",
            chat_id,
        )

        return

    song = queues[chat_id].pop(0)

    await play_song(
        chat_id,
        song,
    )


# ============================================================
# STARTUP MESSAGE
# ============================================================

async def send_startup_message(bot_user, assistant_user):

    try:
        chat = await bot.get_chat(GROUP_ID)

        text = (
            "🤖 MUSIC BOT STARTED\n"
            "\n"
            "🤖 Bot\n"
            f"├─ ID: {bot_user.id}\n"
            f"└─ Username: @{bot_user.username or 'N/A'}\n"
            "\n"
            "👤 Assistant\n"
            f"├─ ID: {assistant_user.id}\n"
            f"└─ Username: @{assistant_user.username or 'N/A'}\n"
            "\n"
            "🟢 Bot: Online\n"
            "🟢 Assistant: Online\n"
            "🟢 PyTgCalls: Ready\n"
            "🎵 Music System: Ready"
        )

        await bot.send_message(
            chat.id,
            text,
        )

    except Exception as error:
        logger.exception(
            "Failed to send startup message: %s",
            error,
        )


# ============================================================
# /START
# ============================================================

@bot.on_message(filters.command("start"))
async def start_command(_, message: Message):

    text = (
        "🎵 **Music Bot Online!**\n"
        "\n"
        "Use `/play song name` to play music.\n"
        "\n"
        "Commands:\n"
        "• `/play <song>` - Play music\n"
        "• `/queue` - Show queue\n"
        "• `/now` - Current song\n"
        "• `/skip` - Skip current song\n"
        "• `/pause` - Pause playback\n"
        "• `/resume` - Resume playback\n"
        "• `/stop` - Stop music\n"
        "• `/help` - Show help"
    )

    await message.reply_text(text)


# ============================================================
# /HELP
# ============================================================

@bot.on_message(filters.command("help"))
async def help_command(_, message: Message):

    text = (
        "🎵 **Music Bot Commands**\n"
        "\n"
        "`/play <song>`\n"
        "Search and play a song from YouTube Music.\n"
        "\n"
        "`/queue`\n"
        "Show the current music queue.\n"
        "\n"
        "`/now`\n"
        "Show currently playing song.\n"
        "\n"
        "`/skip`\n"
        "Skip current song.\n"
        "\n"
        "`/pause`\n"
        "Pause playback.\n"
        "\n"
        "`/resume`\n"
        "Resume playback.\n"
        "\n"
        "`/stop`\n"
        "Stop playback and clear queue."
    )

    await message.reply_text(text)


# ============================================================
# /PLAY
# ============================================================

@bot.on_message(filters.command("play"))
async def play_command(_, message: Message):

    if len(message.command) < 2:

        await message.reply_text(
            "❌ Usage:\n"
            "`/play song name`"
        )

        return

    query = " ".join(message.command[1:]).strip()

    if not query:

        await message.reply_text(
            "❌ Please enter a song name."
        )

        return

    status = await message.reply_text(
        f"🔎 Searching YouTube Music for:\n"
        f"`{query}`"
    )

    songs = await search_song(query)

    if not songs:

        await status.edit_text(
            "❌ No results found."
        )

        return

    song = songs[0]

    chat_id = message.chat.id

    # ========================================================
    # If something is already playing
    # ========================================================

    if chat_id in current_song:

        queues.setdefault(
            chat_id,
            [],
        ).append(song)

        position = len(
            queues[chat_id]
        )

        await status.edit_text(
            "🎵 **Added to queue**\n"
            "\n"
            f"🎶 {song.title}\n"
            f"👤 {song.artist}\n"
            f"⏱ {song.duration}\n"
            "\n"
            f"📋 Position: {position}"
        )

        return

    # ========================================================
    # Start playback
    # ========================================================

    await status.edit_text(
        "⏳ Preparing playback...\n\n"
        f"🎶 {song.title}\n"
        f"👤 {song.artist}"
    )

    await play_song(
        chat_id,
        song,
    )

    if chat_id in current_song:

        await status.edit_text(
            "▶️ **Now Playing**\n"
            "\n"
            f"🎶 {song.title}\n"
            f"👤 {song.artist}\n"
            f"⏱ {song.duration}"
        )

    else:

        await status.edit_text(
            "❌ Failed to start playback."
        )


# ============================================================
# /QUEUE
# ============================================================

@bot.on_message(filters.command("queue"))
async def queue_command(_, message: Message):

    chat_id = message.chat.id

    current = current_song.get(chat_id)
    queue = queues.get(chat_id, [])

    if not current and not queue:

        await message.reply_text(
            "📭 Queue is empty."
        )

        return

    text = "🎵 **Music Queue**\n\n"

    if current:

        text += (
            "▶️ **Now Playing**\n"
            f"🎶 {current.title}\n"
            f"👤 {current.artist}\n"
            f"⏱ {current.duration}\n"
            "\n"
        )

    if queue:

        text += "📋 **Up Next**\n\n"

        for index, song in enumerate(
            queue,
            start=1,
        ):

            text += (
                f"{index}. {song.title}\n"
                f"   👤 {song.artist}\n"
                f"   ⏱ {song.duration}\n\n"
            )

    await message.reply_text(text)


# ============================================================
# /NOW
# ============================================================

@bot.on_message(filters.command("now"))
async def now_command(_, message: Message):

    chat_id = message.chat.id

    song = current_song.get(chat_id)

    if not song:

        await message.reply_text(
            "❌ Nothing is playing right now."
        )

        return

    await message.reply_text(
        "▶️ **Now Playing**\n"
        "\n"
        f"🎶 {song.title}\n"
        f"👤 {song.artist}\n"
        f"⏱ {song.duration}"
    )


# ============================================================
# /SKIP
# ============================================================

@bot.on_message(filters.command("skip"))
async def skip_command(_, message: Message):

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

    except Exception as error:

        logger.warning(
            "leave_call during skip failed: %s",
            error,
        )

    current_song.pop(
        chat_id,
        None,
    )

    await message.reply_text(
        "⏭️ Skipped."
    )

    await asyncio.sleep(1)

    if queues.get(chat_id):

        await play_next(
            chat_id
        )

    else:

        await message.reply_text(
            "📭 Queue is empty."
        )


# ============================================================
# /PAUSE
# ============================================================

@bot.on_message(filters.command("pause"))
async def pause_command(_, message: Message):

    chat_id = message.chat.id

    if chat_id not in current_song:

        await message.reply_text(
            "❌ Nothing is playing."
        )

        return

    try:

        await voice.pause(
            chat_id
        )

        await message.reply_text(
            "⏸️ Playback paused."
        )

    except Exception as error:

        logger.exception(
            "Pause failed: %s",
            error,
        )

        await message.reply_text(
            "❌ Failed to pause playback."
        )


# ============================================================
# /RESUME
# ============================================================

@bot.on_message(filters.command("resume"))
async def resume_command(_, message: Message):

    chat_id = message.chat.id

    if chat_id not in current_song:

        await message.reply_text(
            "❌ Nothing is playing."
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
            "Resume failed: %s",
            error,
        )

        await message.reply_text(
            "❌ Failed to resume playback."
        )


# ============================================================
# /STOP
# ============================================================

@bot.on_message(filters.command("stop"))
async def stop_command(_, message: Message):

    chat_id = message.chat.id

    try:

        await voice.leave_call(
            chat_id
        )

    except Exception as error:

        logger.warning(
            "leave_call during stop failed: %s",
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
        "⏹️ Music stopped.\n"
        "🗑️ Queue cleared."
    )


# ============================================================
# STREAM ENDED
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
        "Stream ended in chat %s",
        chat_id,
    )

    current_song.pop(
        chat_id,
        None,
    )

    await asyncio.sleep(1)

    if queues.get(chat_id):

        logger.info(
            "Playing next queued song in %s",
            chat_id,
        )

        await play_next(
            chat_id
        )

    else:

        logger.info(
            "No more songs in queue for %s",
            chat_id,
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "======================================"
    )

    logger.info(
        "Starting Music Bot..."
    )

    logger.info(
        "======================================"
    )

    # --------------------------------------------------------
    # START BOT
    # --------------------------------------------------------

    await bot.start()

    bot_user = await bot.get_me()

    logger.info(
        "Bot started: @%s | ID: %s",
        bot_user.username or "N/A",
        bot_user.id,
    )

    # --------------------------------------------------------
    # START ASSISTANT
    # --------------------------------------------------------

    await assistant.start()

    assistant_user = await assistant.get_me()

    logger.info(
        "Assistant started: @%s | ID: %s",
        assistant_user.username or "N/A",
        assistant_user.id,
    )

    # --------------------------------------------------------
    # START PYTGCalls
    #
    # IMPORTANT:
    # start() is async in the installed PyTgCalls version.
    # --------------------------------------------------------

    await voice.start()

    logger.info(
        "PyTgCalls started successfully."
    )

    # --------------------------------------------------------
    # SEND STARTUP MESSAGE
    # --------------------------------------------------------

    try:

        await send_startup_message(
            bot_user,
            assistant_user,
        )

        logger.info(
            "Startup message sent successfully."
        )

    except Exception as error:

        logger.exception(
            "Startup group message failed: %s",
            error,
        )

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    logger.info(
        "======================================"
    )

    logger.info(
        "MUSIC BOT IS READY"
    )

    logger.info(
        "======================================"
    )

    # --------------------------------------------------------
    # KEEP PROCESS ALIVE
    # --------------------------------------------------------

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
            "Music Bot stopped."
        )
