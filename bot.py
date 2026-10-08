import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message

from pytgcalls import PyTgCalls, filters as tg_filters
from pytgcalls.types import AudioQuality, MediaStream, StreamEnded

from ytmusicapi import YTMusic

from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    ASSISTANT_SESSION,
    GROUP_ID,
    TUNELIO_API_KEY,
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

        songs = []

        for item in results:
            video_id = item.get("videoId")

            if not video_id:
                continue

            title = item.get("title", "Unknown Title")

            artists = item.get("artists", [])

            if artists:
                artist = ", ".join(
                    artist_data.get("name", "Unknown")
                    for artist_data in artists
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

    except Exception:
        logger.exception("YouTube Music search failed")
        return []


# ============================================================
# TUNELIO API
# ============================================================

TUNELIO_URL = "https://tunelio.dev/create"


async def get_tunelio_audio_url(song: Song) -> Optional[str]:
    """
    Ask Tunelio for a fresh signed audio URL.

    Tunelio:
        GET /create
        quality=mp3

    The returned URL is temporary/signed, so we request it
    immediately before playback instead of storing it in queue.
    """

    headers = {
        "Authorization": f"Bearer {TUNELIO_API_KEY}",
        "Accept": "application/json",
    }

    params = {
        "url": song.youtube_url,
        "quality": "mp3",
    }

    timeout = aiohttp.ClientTimeout(
        total=60,
        connect=20,
    )

    try:
        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(
                TUNELIO_URL,
                params=params,
                headers=headers,
            ) as response:

                text = await response.text()

                if response.status != 200:
                    logger.error(
                        "Tunelio API HTTP %s: %s",
                        response.status,
                        text[:1000],
                    )
                    return None

                try:
                    data = await response.json(
                        content_type=None
                    )
                except Exception:
                    logger.error(
                        "Tunelio returned invalid JSON: %s",
                        text[:1000],
                    )
                    return None

                if data.get("status") != "ok":
                    logger.error(
                        "Tunelio API error: %s",
                        data,
                    )
                    return None

                stream_url = data.get("url")

                if not stream_url:
                    logger.error(
                        "Tunelio response has no URL: %s",
                        data,
                    )
                    return None

                logger.info(
                    "Tunelio URL created for: %s",
                    song.title,
                )

                return stream_url

    except asyncio.TimeoutError:
        logger.error(
            "Tunelio request timed out for: %s",
            song.title,
        )
        return None

    except aiohttp.ClientError as error:
        logger.error(
            "Tunelio network error: %s",
            error,
        )
        return None

    except Exception:
        logger.exception(
            "Unexpected Tunelio error"
        )
        return None


# ============================================================
# PLAYBACK
# ============================================================

async def play_song(
    chat_id: int,
    song: Song,
) -> bool:

    async with get_lock(chat_id):

        current_song[chat_id] = song

        logger.info(
            "Resolving audio through Tunelio: %s",
            song.title,
        )

        stream_url = await get_tunelio_audio_url(song)

        if not stream_url:
            current_song.pop(chat_id, None)

            try:
                await bot.send_message(
                    chat_id,
                    (
                        "❌ **Audio extraction failed.**\n\n"
                        f"🎵 `{song.title}`\n\n"
                        "Tunelio could not provide a stream URL."
                    ),
                )
            except Exception:
                logger.exception(
                    "Failed to send extraction error"
                )

            return False

        try:
            media_stream = MediaStream(
                stream_url,
                AudioQuality.HIGH,
            )

            await voice.play(
                chat_id,
                media_stream,
            )

            logger.info(
                "Now playing: %s",
                song.title,
            )

            return True

        except Exception:
            logger.exception(
                "Voice playback failed for %s",
                song.title,
            )

            current_song.pop(chat_id, None)

            try:
                await bot.send_message(
                    chat_id,
                    (
                        "❌ **Playback failed.**\n\n"
                        f"🎵 `{song.title}`"
                    ),
                )
            except Exception:
                pass

            return False


async def play_next(chat_id: int):
    if not queues.get(chat_id):
        current_song.pop(chat_id, None)

        try:
            await voice.leave_call(chat_id)
        except Exception:
            pass

        return

    song = queues[chat_id].pop(0)

    success = await play_song(
        chat_id,
        song,
    )

    if success:
        try:
            await bot.send_message(
                chat_id,
                (
                    "🎵 **Now Playing**\n\n"
                    f"🎧 **{song.title}**\n"
                    f"👤 {song.artist}\n"
                    f"⏱ {song.duration}"
                ),
            )
        except Exception:
            pass

    else:
        # Try next song automatically.
        if queues.get(chat_id):
            await asyncio.sleep(1)
            await play_next(chat_id)


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

    current_song.pop(chat_id, None)

    await asyncio.sleep(1)

    if queues.get(chat_id):
        await play_next(chat_id)
    else:
        try:
            await voice.leave_call(chat_id)
        except Exception:
            pass


# ============================================================
# /START
# ============================================================

@bot.on_message(filters.command("start"))
async def start_handler(
    _,
    message: Message,
):
    await message.reply_text(
        "🎵 **Zelmo Music Bot**\n\n"
        "YouTube Music powered music player.\n\n"
        "▶️ `/play song name`\n"
        "📋 `/queue`\n"
        "🎵 `/now`\n"
        "⏭ `/skip`\n"
        "⏸ `/pause`\n"
        "▶️ `/resume`\n"
        "⏹ `/stop`\n"
        "ℹ️ `/help`"
    )


# ============================================================
# /HELP
# ============================================================

@bot.on_message(filters.command("help"))
async def help_handler(
    _,
    message: Message,
):
    await message.reply_text(
        "🎵 **Music Commands**\n\n"
        "▶️ `/play <song>` - Search and play\n"
        "📋 `/queue` - Show queue\n"
        "🎵 `/now` - Current song\n"
        "⏭ `/skip` - Skip current song\n"
        "⏸ `/pause` - Pause playback\n"
        "▶️ `/resume` - Resume playback\n"
        "⏹ `/stop` - Stop and clear queue\n\n"
        "Example:\n"
        "`/play Arijit Singh Tum Hi Ho`"
    )


# ============================================================
# /PLAY
# ============================================================

@bot.on_message(filters.command("play"))
async def play_handler(
    _,
    message: Message,
):
    if len(message.command) < 2:
        await message.reply_text(
            "❌ Please provide a song name.\n\n"
            "Example:\n"
            "`/play Tum Hi Ho`"
        )
        return

    query = " ".join(message.command[1:]).strip()

    status = await message.reply_text(
        f"🔎 Searching YouTube Music for:\n`{query}`"
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

    # If something is currently playing, add to queue.
    if chat_id in current_song:

        queues[chat_id].append(song)

        position = len(queues[chat_id])

        await safe_edit(
            status,
            (
                "✅ **Added to Queue**\n\n"
                f"🎵 **{song.title}**\n"
                f"👤 {song.artist}\n"
                f"⏱ {song.duration}\n\n"
                f"📋 Position: `{position}`"
            ),
        )

        return

    await safe_edit(
        status,
        (
            "⏳ **Preparing audio...**\n\n"
            f"🎵 **{song.title}**\n"
            f"👤 {song.artist}\n\n"
            "🔗 Resolving stream..."
        ),
    )

    success = await play_song(
        chat_id,
        song,
    )

    if not success:
        return

    await safe_edit(
        status,
        (
            "▶️ **Now Playing**\n\n"
            f"🎵 **{song.title}**\n"
            f"👤 {song.artist}\n"
            f"⏱ {song.duration}"
        ),
    )


# ============================================================
# /QUEUE
# ============================================================

@bot.on_message(filters.command("queue"))
async def queue_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    queue = queues.get(chat_id, [])

    if not queue:
        await message.reply_text(
            "📋 **Queue is empty.**"
        )
        return

    text = "📋 **Music Queue**\n\n"

    for index, song in enumerate(queue, start=1):
        text += (
            f"`{index}.` 🎵 **{song.title}**\n"
            f"     👤 {song.artist}\n"
            f"     ⏱ {song.duration}\n\n"
        )

    await message.reply_text(text)


# ============================================================
# /NOW
# ============================================================

@bot.on_message(filters.command("now"))
async def now_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    song = current_song.get(chat_id)

    if not song:
        await message.reply_text(
            "❌ Nothing is playing."
        )
        return

    await message.reply_text(
        "🎵 **Now Playing**\n\n"
        f"🎧 **{song.title}**\n"
        f"👤 {song.artist}\n"
        f"⏱ {song.duration}"
    )


# ============================================================
# /SKIP
# ============================================================

@bot.on_message(filters.command("skip"))
async def skip_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    if not current_song.get(chat_id):
        await message.reply_text(
            "❌ Nothing is playing."
        )
        return

    try:
        await voice.leave_call(chat_id)
    except Exception:
        pass

    current_song.pop(chat_id, None)

    await message.reply_text(
        "⏭ **Skipped.**"
    )

    await asyncio.sleep(1)

    if queues.get(chat_id):
        await play_next(chat_id)


# ============================================================
# /PAUSE
# ============================================================

@bot.on_message(filters.command("pause"))
async def pause_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    try:
        await voice.pause(chat_id)

        await message.reply_text(
            "⏸ **Playback paused.**"
        )

    except Exception as error:
        logger.error(
            "Pause error: %s",
            error,
        )

        await message.reply_text(
            "❌ Unable to pause playback."
        )


# ============================================================
# /RESUME
# ============================================================

@bot.on_message(filters.command("resume"))
async def resume_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    try:
        await voice.resume(chat_id)

        await message.reply_text(
            "▶️ **Playback resumed.**"
        )

    except Exception as error:
        logger.error(
            "Resume error: %s",
            error,
        )

        await message.reply_text(
            "❌ Unable to resume playback."
        )


# ============================================================
# /STOP
# ============================================================

@bot.on_message(filters.command("stop"))
async def stop_handler(
    _,
    message: Message,
):
    chat_id = message.chat.id

    queues.pop(chat_id, None)
    current_song.pop(chat_id, None)

    try:
        await voice.leave_call(chat_id)
    except Exception:
        pass

    await message.reply_text(
        "⏹ **Playback stopped.**\n"
        "🗑 Queue cleared."
    )


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

        # ---------------- BOT ----------------

        bot_text = (
            "🤖 **BOT STARTED**\n"
            "\n"
            f"🆔 ID: `{bot_user.id}`\n"
            f"👤 Username: @{bot_user.username or 'N/A'}\n"
            "\n"
            "🟢 Status: Online\n"
            "🎵 Music Bot: Ready\n"
            "🔎 YouTube Music: Ready\n"
            "🌐 Tunelio API: Ready"
        )

        await bot.send_message(
            chat_id,
            bot_text,
        )

        # ---------------- ASSISTANT ----------------

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

    except Exception:
        logger.exception(
            "Startup messages failed"
        )


# ============================================================
# MAIN
# ============================================================

async def main():
    logger.info(
        "Starting Telegram Music Bot..."
    )

    await bot.start()

    bot_user = await bot.get_me()

    logger.info(
        "Bot started: @%s | ID: %s",
        bot_user.username,
        bot_user.id,
    )

    await assistant.start()

    assistant_user = await assistant.get_me()

    logger.info(
        "Assistant started: @%s | ID: %s",
        assistant_user.username,
        assistant_user.id,
    )

    await voice.start()

    logger.info(
        "PyTgCalls started successfully."
    )

    await send_startup_messages(
        bot_user,
        assistant_user,
    )

    logger.info(
        "MUSIC BOT IS READY"
    )

    await asyncio.Event().wait()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info(
            "Bot stopped by user."
        )

    except Exception:
        logger.exception(
            "Fatal error"
        )
