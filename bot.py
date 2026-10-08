import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from pyrogram import Client, filters
from pyrogram.types import Message

from pytgcalls import PyTgCalls
from pytgcalls import filters as tg_filters
from pytgcalls.types import MediaStream, AudioQuality
from pytgcalls.types.stream import StreamEnded

from ytmusicapi import YTMusic

from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    ASSISTANT_SESSION,
    GROUP_ID,
    COOKIES_PATH,
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
# GLOBALS
# ============================================================

bot: Optional[Client] = None
assistant: Optional[Client] = None
voice: Optional[PyTgCalls] = None

ytmusic = YTMusic()

queues: Dict[int, List["Song"]] = {}
current_song: Dict[int, "Song"] = {}
play_locks: Dict[int, asyncio.Lock] = {}


# ============================================================
# SONG
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


# ============================================================
# SEARCH YOUTUBE MUSIC
# ============================================================

async def search_song(query: str) -> List[Song]:
    """
    Search YouTube Music and return matching songs.
    """

    def do_search():
        return ytmusic.search(
            query,
            filter="songs",
            limit=5,
        )

    results = await asyncio.to_thread(do_search)

    songs: List[Song] = []

    for item in results:
        video_id = item.get("videoId")

        if not video_id:
            continue

        title = item.get("title", "Unknown")

        artists = item.get("artists", [])

        if artists:
            artist = ", ".join(
                artist.get("name", "Unknown")
                for artist in artists
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
# SAFE EDIT
# ============================================================

async def safe_edit(
    message: Message,
    text: str,
):
    try:
        if message.text == text:
            return message

        return await message.edit_text(text)

    except Exception as error:
        if "MESSAGE_NOT_MODIFIED" in str(error):
            return message

        raise


# ============================================================
# CHECK COOKIES
# ============================================================

def check_cookies():
    if not os.path.isfile(COOKIES_PATH):
        raise RuntimeError(
            f"YouTube cookies file not found: {COOKIES_PATH}"
        )

    logger.info(
        "YouTube cookies found: %s",
        COOKIES_PATH,
    )


# ============================================================
# CREATE STREAM
# ============================================================

def create_stream(song: Song) -> MediaStream:
    """
    Create a PyTgCalls stream using yt-dlp + YouTube cookies.
    """

    if not os.path.isfile(COOKIES_PATH):
        raise RuntimeError(
            f"Cookies file not found: {COOKIES_PATH}"
        )

    logger.info(
        "Creating yt-dlp stream for: %s",
        song.title,
    )

    ytdlp_parameters = (
        "--no-playlist "
        "--extract-audio "
        f'--cookies "{COOKIES_PATH}" '
    )

    return MediaStream(
        song.youtube_url,
        AudioQuality.HIGH,
        ytdlp_parameters=ytdlp_parameters,
    )


# ============================================================
# PLAY SONG
# ============================================================

async def play_song(
    chat_id: int,
    song: Song,
):
    if voice is None:
        raise RuntimeError("PyTgCalls is not initialized.")

    current_song[chat_id] = song

    logger.info(
        "Starting playback: %s | %s",
        song.title,
        song.youtube_url,
    )

    stream = create_stream(song)

    await voice.play(
        chat_id,
        stream,
    )

    logger.info(
        "Playback started: %s",
        song.title,
    )


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(chat_id: int):
    lock = play_locks.setdefault(
        chat_id,
        asyncio.Lock(),
    )

    async with lock:
        if not queues.get(chat_id):
            current_song.pop(chat_id, None)
            return

        song = queues[chat_id].pop(0)

        try:
            await play_song(
                chat_id,
                song,
            )

        except Exception:
            current_song.pop(chat_id, None)

            logger.exception(
                "Playback failed for: %s",
                song.title,
            )

            if queues.get(chat_id):
                await asyncio.sleep(1)
                await play_next(chat_id)


# ============================================================
# /START
# ============================================================

async def start_command(
    client: Client,
    message: Message,
):
    await message.reply_text(
        "🎵 **Music Bot**\n\n"
        "Use `/play song name` to play music.\n\n"
        "Commands:\n"
        "▶️ `/play <song>`\n"
        "📋 `/queue`\n"
        "🎵 `/now`\n"
        "⏭ `/skip`\n"
        "⏸ `/pause`\n"
        "▶️ `/resume`\n"
        "⏹ `/stop`\n"
        "❓ `/help`"
    )


# ============================================================
# /HELP
# ============================================================

async def help_command(
    client: Client,
    message: Message,
):
    await message.reply_text(
        "🎵 **Music Bot Commands**\n\n"
        "`/play <song>` - Search and play music\n"
        "`/queue` - Show queue\n"
        "`/now` - Current song\n"
        "`/skip` - Skip current song\n"
        "`/pause` - Pause playback\n"
        "`/resume` - Resume playback\n"
        "`/stop` - Stop and clear queue"
    )


# ============================================================
# /PLAY
# ============================================================

async def play_command(
    client: Client,
    message: Message,
):
    if len(message.command) < 2:
        await message.reply_text(
            "❌ Usage:\n"
            "`/play song name`"
        )
        return

    query = " ".join(
        message.command[1:]
    ).strip()

    chat_id = message.chat.id

    status = await message.reply_text(
        f"🔎 Searching YouTube Music...\n\n"
        f"🎵 `{query}`"
    )

    try:
        songs = await search_song(query)

    except Exception:
        logger.exception(
            "YouTube Music search failed."
        )

        await safe_edit(
            status,
            "❌ YouTube Music search failed.",
        )

        return

    if not songs:
        await safe_edit(
            status,
            "❌ No song found.",
        )
        return

    song = songs[0]

    queue = queues.setdefault(
        chat_id,
        [],
    )

    queue.append(song)

    # Nothing currently playing
    if chat_id not in current_song:

        await safe_edit(
            status,
            "⏳ **Preparing playback...**\n\n"
            f"🎵 **{song.title}**\n"
            f"👤 {song.artist}\n"
            f"🆔 `{song.video_id}`",
        )

        await play_next(chat_id)

        await safe_edit(
            status,
            "▶️ **Now Playing**\n\n"
            f"🎵 **{song.title}**\n"
            f"👤 {song.artist}\n"
            f"⏱ {song.duration}\n\n"
            "🎤 Voice Chat",
        )

    else:

        position = len(queue)

        await safe_edit(
            status,
            "➕ **Added to Queue**\n\n"
            f"🎵 **{song.title}**\n"
            f"👤 {song.artist}\n"
            f"📋 Position: `{position}`",
        )


# ============================================================
# /QUEUE
# ============================================================

async def queue_command(
    client: Client,
    message: Message,
):
    chat_id = message.chat.id

    queue = queues.get(
        chat_id,
        [],
    )

    current = current_song.get(chat_id)

    lines = [
        "📋 **Music Queue**",
        "",
    ]

    if current:
        lines.extend(
            [
                "▶️ **Now Playing**",
                f"🎵 {current.title}",
                f"👤 {current.artist}",
                "",
            ]
        )

    if not queue:
        lines.append(
            "📭 Queue is empty."
        )

    else:
        for index, song in enumerate(
            queue[:20],
            start=1,
        ):
            lines.append(
                f"`{index}.` {song.title} — {song.artist}"
            )

    await message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# /NOW
# ============================================================

async def now_command(
    client: Client,
    message: Message,
):
    chat_id = message.chat.id

    song = current_song.get(chat_id)

    if not song:
        await message.reply_text(
            "📭 Nothing is playing."
        )
        return

    await message.reply_text(
        "🎵 **Now Playing**\n\n"
        f"🎧 **{song.title}**\n"
        f"👤 {song.artist}\n"
        f"⏱ {song.duration}\n\n"
        f"🔗 {song.youtube_url}"
    )


# ============================================================
# /SKIP
# ============================================================

async def skip_command(
    client: Client,
    message: Message,
):
    if voice is None:
        return

    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "📭 Nothing is playing."
        )
        return

    try:
        await voice.leave_call(
            chat_id
        )
    except Exception:
        logger.exception(
            "Failed to leave voice chat during skip."
        )

    current_song.pop(
        chat_id,
        None,
    )

    await message.reply_text(
        "⏭ **Skipped.**"
    )

    if queues.get(chat_id):
        await play_next(chat_id)


# ============================================================
# /PAUSE
# ============================================================

async def pause_command(
    client: Client,
    message: Message,
):
    if voice is None:
        return

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
            "Pause failed."
        )

        await message.reply_text(
            f"❌ Pause failed:\n`{error}`"
        )


# ============================================================
# /RESUME
# ============================================================

async def resume_command(
    client: Client,
    message: Message,
):
    if voice is None:
        return

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
            "Resume failed."
        )

        await message.reply_text(
            f"❌ Resume failed:\n`{error}`"
        )


# ============================================================
# /STOP
# ============================================================

async def stop_command(
    client: Client,
    message: Message,
):
    if voice is None:
        return

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
        logger.exception(
            "Stop/leave call failed."
        )

    await message.reply_text(
        "⏹ **Stopped.**\n"
        "🗑 Queue cleared."
    )


# ============================================================
# VOICE CHAT STREAM END
# ============================================================

async def stream_ended_handler(
    client,
    update: StreamEnded,
):
    chat_id = update.chat_id

    logger.info(
        "Stream ended in chat: %s",
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
# REGISTER HANDLERS
# ============================================================

def register_handlers():
    global bot
    global voice

    if bot is None:
        raise RuntimeError(
            "Bot is not initialized."
        )

    if voice is None:
        raise RuntimeError(
            "Voice is not initialized."
        )

    # ----------------------------
    # Bot handlers
    # ----------------------------

    bot.add_handler(
        __import__(
            "pyrogram.handlers",
            fromlist=["MessageHandler"],
        ).MessageHandler(
            start_command,
            filters.command("start"),
        )
    )

    bot.add_handler(
        __import__(
            "pyrogram.handlers",
            fromlist=["MessageHandler"],
        ).MessageHandler(
            help_command,
            filters.command("help"),
        )
    )

    bot.add_handler(
        __import__(
            "pyrogram.handlers",
            fromlist=["MessageHandler"],
        ).MessageHandler(
            play_command,
            filters.command("play"),
        )
    )

    bot.add_handler(
        __import__(
            "pyrogram.handlers",
            fromlist=["MessageHandler"],
        ).MessageHandler(
            queue_command,
            filters.command("queue"),
        )
    )

    bot.add_handler(
        __import__(
            "pyrogram.handlers",
            fromlist=["MessageHandler"],
        ).MessageHandler(
            now_command,
            filters.command("now"),
        )
    )

    bot.add_handler(
        __import__(
            "pyrogram.handlers",
            fromlist=["MessageHandler"],
        ).MessageHandler(
            skip_command,
            filters.command("skip"),
        )
    )

    bot.add_handler(
        __import__(
            "pyrogram.handlers",
            fromlist=["MessageHandler"],
        ).MessageHandler(
            pause_command,
            filters.command("pause"),
        )
    )

    bot.add_handler(
        __import__(
            "pyrogram.handlers",
            fromlist=["MessageHandler"],
        ).MessageHandler(
            resume_command,
            filters.command("resume"),
        )
    )

    bot.add_handler(
        __import__(
            "pyrogram.handlers",
            fromlist=["MessageHandler"],
        ).MessageHandler(
            stop_command,
            filters.command("stop"),
        )
    )

    # ----------------------------
    # PyTgCalls handler
    # ----------------------------

    voice.on_update(
        tg_filters.stream_end()
    )(stream_ended_handler)

    logger.info(
        "All handlers registered."
    )


# ============================================================
# STARTUP MESSAGES
# ============================================================

async def send_startup_messages(
    bot_user,
    assistant_user,
):
    if bot is None or assistant is None:
        return

    try:
        chat = await bot.get_chat(
            GROUP_ID
        )

        chat_id = chat.id

        # Bot startup
        bot_text = (
            "🤖 **BOT STARTED**\n\n"
            f"🆔 ID: `{bot_user.id}`\n"
            f"👤 Username: @{bot_user.username or 'N/A'}\n\n"
            "🟢 Status: Online\n"
            "🎵 Music Bot: Ready"
        )

        await bot.send_message(
            chat_id,
            bot_text,
        )

        # Assistant startup
        assistant_text = (
            "👤 **ASSISTANT STARTED**\n\n"
            f"🆔 ID: `{assistant_user.id}`\n"
            f"👤 Username: @{assistant_user.username or 'N/A'}\n\n"
            "🟢 Status: Online\n"
            "🎤 Voice Chat: Ready\n"
            "🎵 Music System: Ready"
        )

        await assistant.send_message(
            chat_id,
            assistant_text,
        )

    except Exception as error:
        logger.exception(
            "Startup messages failed: %s",
            error,
        )


# ============================================================
# MAIN
# ============================================================

async def main():
    global bot
    global assistant
    global voice

    # --------------------------------------------------------
    # IMPORTANT:
    # Create all Pyrogram/PyTgCalls objects INSIDE the same
    # asyncio event loop.
    # --------------------------------------------------------

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

    voice = PyTgCalls(
        assistant
    )

    # --------------------------------------------------------
    # Register handlers while everything belongs to this loop.
    # --------------------------------------------------------

    register_handlers()

    # --------------------------------------------------------
    # Check cookies
    # --------------------------------------------------------

    check_cookies()

    # --------------------------------------------------------
    # Start bot
    # --------------------------------------------------------

    await bot.start()

    bot_user = await bot.get_me()

    logger.info(
        "Bot started: @%s | ID: %s",
        bot_user.username,
        bot_user.id,
    )

    # --------------------------------------------------------
    # Start assistant
    # --------------------------------------------------------

    await assistant.start()

    assistant_user = await assistant.get_me()

    logger.info(
        "Assistant started: @%s | ID: %s",
        assistant_user.username,
        assistant_user.id,
    )

    # --------------------------------------------------------
    # Start PyTgCalls
    # --------------------------------------------------------

    await voice.start()

    logger.info(
        "PyTgCalls started successfully."
    )

    # --------------------------------------------------------
    # Send startup messages
    # --------------------------------------------------------

    await send_startup_messages(
        bot_user,
        assistant_user,
    )

    logger.info(
        "MUSIC BOT IS READY"
    )

    # --------------------------------------------------------
    # Keep application alive
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
            "Music bot stopped."
        )

    except Exception:
        logger.exception(
            "Fatal error."
        )
