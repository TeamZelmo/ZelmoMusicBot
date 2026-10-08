import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List

from pyrogram import Client, filters
from pyrogram.types import Message

from pytgcalls import PyTgCalls
from pytgcalls import filters as tg_filters
from pytgcalls.types import MediaStream
from pytgcalls.types import AudioQuality
from pytgcalls.types.stream import StreamEnded

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


voice = PyTgCalls(assistant)

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
# HELPERS
# ============================================================

def get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in play_locks:
        play_locks[chat_id] = asyncio.Lock()

    return play_locks[chat_id]


async def safe_edit(message: Message, text: str):
    try:
        if message.text == text:
            return message

        return await message.edit_text(text)

    except Exception as error:
        if "MESSAGE_NOT_MODIFIED" in str(error):
            return message

        raise


# ============================================================
# YOUTUBE MUSIC SEARCH
# ============================================================

async def search_song(query: str) -> List[Song]:
    try:
        results = await asyncio.to_thread(
            ytmusic.search,
            query,
            filter="songs",
            limit=5,
        )

    except Exception as error:
        logger.exception("YouTube Music search failed: %s", error)
        return []

    songs = []

    for item in results:
        video_id = item.get("videoId")

        if not video_id:
            continue

        title = item.get("title", "Unknown")

        artists = item.get("artists") or []

        if artists:
            artist = ", ".join(
                artist_item.get("name", "")
                for artist_item in artists
                if artist_item.get("name")
            )
        else:
            artist = "Unknown Artist"

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
# YT-DLP STREAM
# ============================================================

def create_stream(song: Song) -> MediaStream:
    """
    Cookies-free YouTube extraction.

    We deliberately avoid --cookies.

    The selected clients are intended to reduce dependency
    on PO tokens for GVS requests.
    """

    ytdlp_parameters = (
        "--no-playlist "
        "--extract-audio "
        "--extractor-args "
        "\"youtube:player_client=android_vr,web_embedded\" "
    )

    return MediaStream(
        song.youtube_url,
        AudioQuality.HIGH,
        ytdlp_parameters=ytdlp_parameters,
    )


# ============================================================
# PLAY SONG
# ============================================================

async def play_song(chat_id: int, song: Song):
    lock = get_lock(chat_id)

    async with lock:

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
                "Playback started in %s: %s",
                chat_id,
                song.title,
            )

        except Exception as error:

            logger.exception(
                "Playback failed in %s: %s",
                chat_id,
                error,
            )

            current_song.pop(chat_id, None)

            raise


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(chat_id: int):

    queue = queues.get(chat_id)

    if not queue:
        current_song.pop(chat_id, None)
        return

    song = queue.pop(0)

    try:
        await play_song(
            chat_id,
            song,
        )

    except Exception as error:

        logger.error(
            "Could not play next song in %s: %s",
            chat_id,
            error,
        )

        if queues.get(chat_id):
            await asyncio.sleep(2)
            await play_next(chat_id)


# ============================================================
# STARTUP MESSAGES
# ============================================================

async def send_startup_messages(
    bot_user,
    assistant_user,
):

    try:

        chat = await bot.get_chat(GROUP_ID)

        chat_id = chat.id

        # ----------------------------------------------------
        # BOT MESSAGE
        # ----------------------------------------------------

        bot_text = (
            "🤖 **BOT STARTED**\n"
            "\n"
            f"🆔 ID: `{bot_user.id}`\n"
            f"👤 Username: @{bot_user.username or 'N/A'}\n"
            "\n"
            "🟢 Status: Online\n"
            "🎵 Music Bot: Ready"
        )

        await bot.send_message(
            chat_id,
            bot_text,
        )

        # ----------------------------------------------------
        # ASSISTANT MESSAGE
        # ----------------------------------------------------

        assistant_text = (
            "👤 **ASSISTANT STARTED**\n"
            "\n"
            f"🆔 ID: `{assistant_user.id}`\n"
            f"👤 Username: @{assistant_user.username or 'N/A'}\n"
            "\n"
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
# /START
# ============================================================

@bot.on_message(filters.command("start"))
async def start_command(_, message: Message):

    await message.reply_text(
        "🎵 **Music Bot**\n"
        "\n"
        "YouTube Music voice chat player.\n"
        "\n"
        "Commands:\n"
        "• `/play <song>`\n"
        "• `/queue`\n"
        "• `/now`\n"
        "• `/skip`\n"
        "• `/pause`\n"
        "• `/resume`\n"
        "• `/stop`\n"
        "• `/help`"
    )


# ============================================================
# /HELP
# ============================================================

@bot.on_message(filters.command("help"))
async def help_command(_, message: Message):

    await message.reply_text(
        "🎵 **Music Commands**\n"
        "\n"
        "`/play song name` - Search and play\n"
        "`/queue` - Show queue\n"
        "`/now` - Current song\n"
        "`/skip` - Skip current song\n"
        "`/pause` - Pause playback\n"
        "`/resume` - Resume playback\n"
        "`/stop` - Stop playback\n"
    )


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

    status = await message.reply_text(
        "🔎 Searching YouTube Music..."
    )

    songs = await search_song(query)

    if not songs:

        await safe_edit(
            status,
            "❌ No songs found.",
        )

        return

    song = songs[0]

    chat_id = message.chat.id

    if chat_id not in queues:
        queues[chat_id] = []

    # --------------------------------------------------------
    # CURRENT PLAYBACK
    # --------------------------------------------------------

    if chat_id in current_song:

        queues[chat_id].append(song)

        await safe_edit(
            status,
            (
                "➕ **Added to queue**\n"
                "\n"
                f"🎵 {song.title}\n"
                f"👤 {song.artist}\n"
                f"⏱ {song.duration}\n"
                "\n"
                f"📋 Position: {len(queues[chat_id])}"
            ),
        )

        return

    # --------------------------------------------------------
    # NOTHING PLAYING
    # --------------------------------------------------------

    try:

        await safe_edit(
            status,
            (
                "🎵 **Starting playback...**\n"
                "\n"
                f"🎵 {song.title}\n"
                f"👤 {song.artist}\n"
                f"⏱ {song.duration}"
            ),
        )

        await play_song(
            chat_id,
            song,
        )

        await safe_edit(
            status,
            (
                "▶️ **Now Playing**\n"
                "\n"
                f"🎵 {song.title}\n"
                f"👤 {song.artist}\n"
                f"⏱ {song.duration}"
            ),
        )

    except Exception:

        await safe_edit(
            status,
            (
                "❌ **Playback failed.**\n"
                "\n"
                "YouTube rejected the stream request.\n"
                "Check the Render logs for yt-dlp details."
            ),
        )


# ============================================================
# /QUEUE
# ============================================================

@bot.on_message(filters.command("queue"))
async def queue_command(_, message: Message):

    chat_id = message.chat.id

    queue = queues.get(chat_id, [])

    current = current_song.get(chat_id)

    if not current and not queue:

        await message.reply_text(
            "📭 Queue is empty."
        )

        return

    text = "🎵 **Music Queue**\n\n"

    if current:

        text += (
            "▶️ **Playing:**\n"
            f"🎵 {current.title}\n"
            f"👤 {current.artist}\n"
            f"⏱ {current.duration}\n\n"
        )

    if queue:

        text += "📋 **Up Next:**\n\n"

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

    song = current_song.get(
        message.chat.id
    )

    if not song:

        await message.reply_text(
            "⏹ Nothing is playing."
        )

        return

    await message.reply_text(
        "🎵 **Now Playing**\n"
        "\n"
        f"🎵 {song.title}\n"
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
            "⏹ Nothing is playing."
        )

        return

    try:

        await voice.leave_call(
            chat_id
        )

    except Exception:
        pass

    current_song.pop(
        chat_id,
        None,
    )

    await message.reply_text(
        "⏭ **Skipped.**"
    )

    await asyncio.sleep(1)

    if queues.get(chat_id):

        await play_next(
            chat_id
        )


# ============================================================
# /PAUSE
# ============================================================

@bot.on_message(filters.command("pause"))
async def pause_command(_, message: Message):

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
            "Pause failed: %s",
            error,
        )

        await message.reply_text(
            f"❌ Pause failed:\n`{error}`"
        )


# ============================================================
# /RESUME
# ============================================================

@bot.on_message(filters.command("resume"))
async def resume_command(_, message: Message):

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
            "Resume failed: %s",
            error,
        )

        await message.reply_text(
            f"❌ Resume failed:\n`{error}`"
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

    except Exception:
        pass

    queues.pop(
        chat_id,
        None,
    )

    current_song.pop(
        chat_id,
        None,
    )

    await message.reply_text(
        "⏹ **Playback stopped.**\n"
        "🗑 Queue cleared."
    )


# ============================================================
# STREAM END
# ============================================================

@voice.on_update(tg_filters.stream_end())
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

        await play_next(
            chat_id
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "Starting Music Bot..."
    )

    # --------------------------------------------------------
    # BOT
    # --------------------------------------------------------

    await bot.start()

    bot_user = await bot.get_me()

    logger.info(
        "Bot started: @%s | ID: %s",
        bot_user.username,
        bot_user.id,
    )

    # --------------------------------------------------------
    # ASSISTANT
    # --------------------------------------------------------

    await assistant.start()

    assistant_user = await assistant.get_me()

    logger.info(
        "Assistant started: @%s | ID: %s",
        assistant_user.username,
        assistant_user.id,
    )

    # --------------------------------------------------------
    # PYTG CALLS
    # --------------------------------------------------------

    await voice.start()

    logger.info(
        "PyTgCalls started successfully."
    )

    # --------------------------------------------------------
    # STARTUP MESSAGES
    # --------------------------------------------------------

    await send_startup_messages(
        bot_user,
        assistant_user,
    )

    logger.info(
        "MUSIC BOT IS READY"
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
            "Bot stopped by user."
        )

    except Exception as error:

        logger.exception(
            "Fatal error: %s",
            error,
        )
