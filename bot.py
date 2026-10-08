import os
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from pytgcalls import PyTgCalls
from pytgcalls import filters as tg_filters
from pytgcalls.types import MediaStream
from pytgcalls.types.stream import StreamEnded


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", "").strip()
GROUP_ID = int(os.getenv("GROUP_ID", "0"))

COOKIES_PATH = os.getenv(
    "COOKIES_PATH",
    "/app/cookies.txt",
).strip()


if not API_ID:
    raise RuntimeError("API_ID is missing.")

if not API_HASH:
    raise RuntimeError("API_HASH is missing.")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")

if not ASSISTANT_SESSION:
    raise RuntimeError("ASSISTANT_SESSION is missing.")

if not GROUP_ID:
    raise RuntimeError("GROUP_ID is missing.")


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


# ============================================================
# DATA
# ============================================================

@dataclass
class Song:
    title: str
    artist: str
    url: str
    duration: str = "Unknown"

    @property
    def youtube_url(self) -> str:
        return self.url


queues: Dict[int, List[Song]] = {}
current_song: Dict[int, Song] = {}
current_message: Dict[int, int] = {}

play_locks: Dict[int, asyncio.Lock] = {}

paused_chats = set()


# ============================================================
# HELPERS
# ============================================================

def get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in play_locks:
        play_locks[chat_id] = asyncio.Lock()

    return play_locks[chat_id]


def format_duration(value: str) -> str:
    if not value:
        return "Unknown"

    value = value.strip()

    if value.lower() in ("none", "nan", "unknown"):
        return "Unknown"

    return value


# ============================================================
# YT-DLP SEARCH
# ============================================================

async def search_song(query: str) -> List[Song]:
    logger.info("yt-dlp search: %s", query)

    command = [
        "yt-dlp",
        "--flat-playlist",
        "--skip-download",
        "--no-warnings",
        "--ignore-errors",
        "--print",
        "%(id)s\t%(title)s\t%(channel)s\t%(duration_string)s",
        f"ytsearch5:{query}",
    ]

    if os.path.isfile(COOKIES_PATH):
        command.insert(1, "--cookies")
        command.insert(2, COOKIES_PATH)

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()

    output = stdout.decode(
        "utf-8",
        errors="ignore",
    ).strip()

    error_output = stderr.decode(
        "utf-8",
        errors="ignore",
    ).strip()

    if process.returncode != 0 and not output:
        logger.error(
            "yt-dlp search failed: %s",
            error_output,
        )

        raise RuntimeError(
            error_output or "yt-dlp search failed."
        )

    songs: List[Song] = []

    for line in output.splitlines():
        parts = line.split("\t", 3)

        if len(parts) < 2:
            continue

        video_id = parts[0].strip()
        title = parts[1].strip()

        artist = (
            parts[2].strip()
            if len(parts) >= 3 and parts[2].strip()
            else "YouTube"
        )

        duration = (
            parts[3].strip()
            if len(parts) >= 4
            else "Unknown"
        )

        if not video_id or not title:
            continue

        url = f"https://www.youtube.com/watch?v={video_id}"

        songs.append(
            Song(
                title=title,
                artist=artist,
                url=url,
                duration=format_duration(duration),
            )
        )

    return songs


# ============================================================
# GET DIRECT AUDIO URL
# ============================================================

async def get_audio_url(song: Song) -> str:
    logger.info(
        "Extracting direct audio URL: %s | %s",
        song.title,
        song.youtube_url,
    )

    command = [
        "yt-dlp",
        "--no-playlist",
        "--format",
        "bestaudio/best",
        "--get-url",
        "--no-warnings",
        "--quiet",
        song.youtube_url,
    ]

    if os.path.isfile(COOKIES_PATH):
        command[1:1] = [
            "--cookies",
            COOKIES_PATH,
        ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()

    audio_url = stdout.decode(
        "utf-8",
        errors="ignore",
    ).strip()

    error_output = stderr.decode(
        "utf-8",
        errors="ignore",
    ).strip()

    if process.returncode != 0:
        logger.error(
            "yt-dlp audio extraction failed: %s",
            error_output,
        )

        raise RuntimeError(
            error_output or
            "yt-dlp failed to extract audio URL."
        )

    if not audio_url:
        raise RuntimeError(
            "yt-dlp returned an empty audio URL."
        )

    logger.info(
        "Direct audio URL extracted successfully."
    )

    return audio_url


# ============================================================
# NOW PLAYING KEYBOARD
# ============================================================

def now_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⏮",
                    callback_data=f"prev:{chat_id}",
                ),
                InlineKeyboardButton(
                    "⏸",
                    callback_data=f"pause:{chat_id}",
                ),
                InlineKeyboardButton(
                    "⏭",
                    callback_data=f"skip:{chat_id}",
                ),
                InlineKeyboardButton(
                    "⏹",
                    callback_data=f"stop:{chat_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "▶️ Resume",
                    callback_data=f"resume:{chat_id}",
                ),
                InlineKeyboardButton(
                    "🔗 YouTube",
                    callback_data=f"youtube:{chat_id}",
                ),
            ],
        ]
    )


# ============================================================
# NOW PLAYING MESSAGE
# ============================================================

async def send_now_playing(chat_id: int):
    song = current_song.get(chat_id)

    if not song:
        return

    old_message_id = current_message.get(chat_id)

    if old_message_id:
        try:
            await bot.delete_messages(
                chat_id,
                old_message_id,
            )
        except Exception:
            pass

    text = (
        "╭─────────────────────╮\n"
        "│   🎧 **NOW PLAYING**   │\n"
        "├─────────────────────┤\n"
        "│\n"
        f"│ 🎵 **{song.title}**\n"
        "│\n"
        f"│ 👤 **{song.artist}**\n"
        f"│ ⏱ **{song.duration}**\n"
        "│\n"
        "│ 🎧 **Voice Chat**\n"
        "╰─────────────────────╯"
    )

    message = await bot.send_message(
        chat_id,
        text,
        reply_markup=now_keyboard(chat_id),
        disable_web_page_preview=True,
    )

    current_message[chat_id] = message.id


# ============================================================
# PLAY SONG
# ============================================================

async def play_song(chat_id: int, song: Song):
    lock = get_lock(chat_id)

    async with lock:
        try:
            current_song[chat_id] = song
            paused_chats.discard(chat_id)

            logger.info(
                "Preparing playback: %s",
                song.title,
            )

            # ------------------------------------------------
            # IMPORTANT:
            # Resolve YouTube -> direct audio URL FIRST.
            # PyTgCalls receives only the direct URL.
            # ------------------------------------------------

            audio_url = await get_audio_url(song)

            logger.info(
                "Starting PyTgCalls playback: %s",
                song.title,
            )

            await voice.play(
                chat_id,
                MediaStream(audio_url),
            )

            logger.info(
                "Playback started: %s",
                song.title,
            )

            await send_now_playing(chat_id)

        except Exception as exc:
            logger.exception(
                "Playback failed: %s",
                song.title,
            )

            current_song.pop(chat_id, None)
            paused_chats.discard(chat_id)

            try:
                await bot.send_message(
                    chat_id,
                    (
                        "❌ **Playback Failed**\n\n"
                        f"🎵 `{song.title}`\n\n"
                        f"⚠️ `{str(exc)[:500]}`"
                    ),
                )
            except Exception:
                pass


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(chat_id: int):
    if not queues.get(chat_id):
        current_song.pop(chat_id, None)
        paused_chats.discard(chat_id)
        return

    song = queues[chat_id].pop(0)

    await play_song(
        chat_id,
        song,
    )


# ============================================================
# /START
# ============================================================

async def start_command(client, message):
    text = (
        "🎧 **Welcome to Music Bot!**\n\n"
        "Search and play YouTube music directly "
        "in Telegram Voice Chat.\n\n"
        "🎵 `/play song name`\n"
        "📜 `/queue`\n"
        "🎧 `/now`\n"
        "⏭ `/skip`\n"
        "⏸ `/pause`\n"
        "▶️ `/resume`\n"
        "⏹ `/stop`\n"
        "ℹ️ `/help`"
    )

    await message.reply_text(text)


# ============================================================
# /HELP
# ============================================================

async def help_command(client, message):
    text = (
        "🎧 **Music Bot Commands**\n\n"
        "🎵 `/play <song>` — Search and play music\n"
        "📜 `/queue` — Show queue\n"
        "🎧 `/now` — Current song\n"
        "⏭ `/skip` — Skip current song\n"
        "⏸ `/pause` — Pause playback\n"
        "▶️ `/resume` — Resume playback\n"
        "⏹ `/stop` — Stop playback and clear queue"
    )

    await message.reply_text(text)


# ============================================================
# /PLAY
# ============================================================

async def play_command(client, message):
    if len(message.command) < 2:
        await message.reply_text(
            "❌ Usage:\n`/play song name`"
        )
        return

    query = " ".join(
        message.command[1:]
    ).strip()

    chat_id = message.chat.id

    searching = await message.reply_text(
        f"🔎 **Searching YouTube...**\n\n`{query}`"
    )

    try:
        songs = await search_song(query)

        if not songs:
            await searching.edit_text(
                "❌ No results found."
            )
            return

        song = songs[0]

        if chat_id not in queues:
            queues[chat_id] = []

        # If nothing is playing, play immediately.
        if chat_id not in current_song:
            await searching.edit_text(
                f"🎵 **Found:**\n\n"
                f"**{song.title}**\n"
                f"👤 {song.artist}\n"
                f"⏱ {song.duration}\n\n"
                "⏳ Preparing playback..."
            )

            await play_song(
                chat_id,
                song,
            )

        else:
            queues[chat_id].append(song)

            position = len(
                queues[chat_id]
            )

            await searching.edit_text(
                "✅ **Added to Queue**\n\n"
                f"🎵 **{song.title}**\n"
                f"👤 {song.artist}\n"
                f"⏱ {song.duration}\n\n"
                f"📍 Position: **{position}**"
            )

    except Exception as exc:
        logger.exception(
            "Play command failed."
        )

        try:
            await searching.edit_text(
                "❌ **Search/Playback Error**\n\n"
                f"`{str(exc)[:800]}`"
            )
        except Exception:
            pass


# ============================================================
# /QUEUE
# ============================================================

async def queue_command(client, message):
    chat_id = message.chat.id

    song = current_song.get(chat_id)
    queue = queues.get(chat_id, [])

    if not song and not queue:
        await message.reply_text(
            "📭 **Queue is empty.**"
        )
        return

    text = "📜 **Music Queue**\n\n"

    if song:
        text += (
            "🎧 **Now Playing**\n"
            f"🎵 {song.title}\n"
            f"👤 {song.artist}\n\n"
        )

    if queue:
        text += "📋 **Up Next:**\n\n"

        for index, queued_song in enumerate(
            queue[:10],
            start=1,
        ):
            text += (
                f"{index}. 🎵 {queued_song.title}\n"
                f"   👤 {queued_song.artist}\n"
                f"   ⏱ {queued_song.duration}\n\n"
            )

        if len(queue) > 10:
            text += (
                f"➕ {len(queue) - 10} more song(s)..."
            )

    await message.reply_text(text)


# ============================================================
# /NOW
# ============================================================

async def now_command(client, message):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "❌ Nothing is playing right now."
        )
        return

    await send_now_playing(chat_id)


# ============================================================
# /SKIP
# ============================================================

async def skip_command(client, message):
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
    except Exception:
        pass

    current_song.pop(chat_id, None)
    paused_chats.discard(chat_id)

    if queues.get(chat_id):
        await message.reply_text(
            "⏭ **Skipped. Playing next...**"
        )

        await play_next(chat_id)

    else:
        await message.reply_text(
            "⏭ **Skipped. Queue is empty.**"
        )


# ============================================================
# /PAUSE
# ============================================================

async def pause_command(client, message):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "❌ Nothing is playing."
        )
        return

    if chat_id in paused_chats:
        await message.reply_text(
            "⏸ Already paused."
        )
        return

    try:
        await voice.pause(
            chat_id
        )

        paused_chats.add(chat_id)

        await message.reply_text(
            "⏸ **Playback paused.**"
        )

    except Exception as exc:
        logger.exception(
            "Pause failed."
        )

        await message.reply_text(
            f"❌ Pause failed:\n`{str(exc)[:500]}`"
        )


# ============================================================
# /RESUME
# ============================================================

async def resume_command(client, message):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "❌ Nothing is playing."
        )
        return

    if chat_id not in paused_chats:
        await message.reply_text(
            "▶️ Playback is already running."
        )
        return

    try:
        await voice.resume(
            chat_id
        )

        paused_chats.discard(chat_id)

        await message.reply_text(
            "▶️ **Playback resumed.**"
        )

    except Exception as exc:
        logger.exception(
            "Resume failed."
        )

        await message.reply_text(
            f"❌ Resume failed:\n`{str(exc)[:500]}`"
        )


# ============================================================
# /STOP
# ============================================================

async def stop_command(client, message):
    chat_id = message.chat.id

    queues.pop(chat_id, None)
    current_song.pop(chat_id, None)
    paused_chats.discard(chat_id)

    old_message_id = current_message.pop(
        chat_id,
        None,
    )

    if old_message_id:
        try:
            await bot.delete_messages(
                chat_id,
                old_message_id,
            )
        except Exception:
            pass

    try:
        await voice.leave_call(
            chat_id
        )
    except Exception:
        pass

    await message.reply_text(
        "⏹ **Stopped.**\n\n"
        "🗑 Queue cleared."
    )


# ============================================================
# CALLBACKS
# ============================================================

async def callback_handler(client, callback_query):
    data = callback_query.data or ""

    try:
        action, chat_id_text = data.split(
            ":",
            1,
        )

        chat_id = int(chat_id_text)

    except Exception:
        await callback_query.answer(
            "Invalid button.",
            show_alert=True,
        )
        return

    # --------------------------------------------------------
    # PREVIOUS
    # --------------------------------------------------------

    if action == "prev":
        await callback_query.answer(
            "⏮ Previous is not available yet.",
            show_alert=True,
        )
        return

    # --------------------------------------------------------
    # PAUSE
    # --------------------------------------------------------

    if action == "pause":
        if chat_id not in current_song:
            await callback_query.answer(
                "Nothing is playing.",
                show_alert=True,
            )
            return

        if chat_id in paused_chats:
            await callback_query.answer(
                "Already paused.",
            )
            return

        try:
            await voice.pause(
                chat_id
            )

            paused_chats.add(chat_id)

            await callback_query.answer(
                "⏸ Paused",
            )

        except Exception as exc:
            await callback_query.answer(
                f"Pause failed: {str(exc)[:100]}",
                show_alert=True,
            )

        return

    # --------------------------------------------------------
    # RESUME
    # --------------------------------------------------------

    if action == "resume":
        if chat_id not in current_song:
            await callback_query.answer(
                "Nothing is playing.",
                show_alert=True,
            )
            return

        if chat_id not in paused_chats:
            await callback_query.answer(
                "Already playing.",
            )
            return

        try:
            await voice.resume(
                chat_id
            )

            paused_chats.discard(chat_id)

            await callback_query.answer(
                "▶️ Resumed",
            )

        except Exception as exc:
            await callback_query.answer(
                f"Resume failed: {str(exc)[:100]}",
                show_alert=True,
            )

        return

    # --------------------------------------------------------
    # SKIP
    # --------------------------------------------------------

    if action == "skip":
        if chat_id not in current_song:
            await callback_query.answer(
                "Nothing is playing.",
                show_alert=True,
            )
            return

        await callback_query.answer(
            "⏭ Skipping...",
        )

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

        paused_chats.discard(
            chat_id
        )

        if queues.get(chat_id):
            await play_next(
                chat_id
            )
        else:
            await bot.send_message(
                chat_id,
                "⏭ **Skipped. Queue is empty.**",
            )

        return

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    if action == "stop":
        queues.pop(
            chat_id,
            None,
        )

        current_song.pop(
            chat_id,
            None,
        )

        paused_chats.discard(
            chat_id
        )

        old_message_id = current_message.pop(
            chat_id,
            None,
        )

        if old_message_id:
            try:
                await bot.delete_messages(
                    chat_id,
                    old_message_id,
                )
            except Exception:
                pass

        try:
            await voice.leave_call(
                chat_id
            )
        except Exception:
            pass

        await callback_query.answer(
            "⏹ Stopped",
        )

        return

    # --------------------------------------------------------
    # YOUTUBE
    # --------------------------------------------------------

    if action == "youtube":
        song = current_song.get(
            chat_id
        )

        if not song:
            await callback_query.answer(
                "No song is playing.",
                show_alert=True,
            )
            return

        await callback_query.answer()

        try:
            await callback_query.message.reply_text(
                f"🔗 **YouTube Link**\n\n"
                f"🎵 {song.title}\n\n"
                f"{song.youtube_url}",
                disable_web_page_preview=True,
            )
        except Exception:
            pass

        return

    await callback_query.answer(
        "Unknown action.",
        show_alert=True,
    )


# ============================================================
# STREAM ENDED
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

    paused_chats.discard(
        chat_id
    )

    await asyncio.sleep(1)

    if queues.get(chat_id):
        await play_next(
            chat_id
        )

    else:
        logger.info(
            "Queue finished: %s",
            chat_id,
        )


# ============================================================
# REGISTER BOT HANDLERS
# ============================================================

bot.add_handler(
    MessageHandler(
        start_command,
        filters.command("start"),
    )
)

bot.add_handler(
    MessageHandler(
        help_command,
        filters.command("help"),
    )
)

bot.add_handler(
    MessageHandler(
        play_command,
        filters.command("play"),
    )
)

bot.add_handler(
    MessageHandler(
        queue_command,
        filters.command("queue"),
    )
)

bot.add_handler(
    MessageHandler(
        now_command,
        filters.command("now"),
    )
)

bot.add_handler(
    MessageHandler(
        skip_command,
        filters.command("skip"),
    )
)

bot.add_handler(
    MessageHandler(
        pause_command,
        filters.command("pause"),
    )
)

bot.add_handler(
    MessageHandler(
        resume_command,
        filters.command("resume"),
    )
)

bot.add_handler(
    MessageHandler(
        stop_command,
        filters.command("stop"),
    )
)

bot.add_handler(
    CallbackQueryHandler(
        callback_handler
    )
)


# ============================================================
# MAIN
# ============================================================

async def main():
    logger.info(
        "Starting Music Bot..."
    )

    # Start everything inside SAME asyncio event loop.
    await bot.start()

    logger.info(
        "Bot started: @%s | ID: %s",
        bot.me.username,
        bot.me.id,
    )

    await assistant.start()

    logger.info(
        "Assistant started: @%s | ID: %s",
        assistant.me.username,
        assistant.me.id,
    )

    await voice.start()

    logger.info(
        "PyTgCalls started successfully."
    )

    # Register stream-end handler
    voice.on_update(
        tg_filters.stream_end()
    )(stream_ended_handler)

    # --------------------------------------------------------
    # STARTUP MESSAGE
    # --------------------------------------------------------

    try:
        await bot.send_message(
            GROUP_ID,
            "🤖 **Music Bot Started Successfully!**\n\n"
            "🎧 Ready to play music in Voice Chat.",
        )
    except Exception:
        logger.exception(
            "Could not send bot startup message."
        )

    try:
        await assistant.send_message(
            GROUP_ID,
            "🎙 **Assistant is Online!**\n\n"
            "Voice Chat playback is ready.",
        )
    except Exception:
        logger.exception(
            "Could not send assistant startup message."
        )

    logger.info(
        "MUSIC BOT IS READY"
    )

    # Keep application alive.
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
            "Fatal error."
        )
