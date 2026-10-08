import os
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from dotenv import load_dotenv
from ytmusicapi import YTMusic

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from pytgcalls import PyTgCalls
from pytgcalls import idle
from pytgcalls.types import MediaStream
from pytgcalls.types.input_stream import AudioQuality
from pytgcalls import filters as tg_filters
from pytgcalls.types import StreamEnded


# ============================================================
# CONFIG
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
        return (
            f"https://www.youtube.com/watch?v={self.video_id}"
        )


queues: Dict[int, List[Song]] = {}
current_song: Dict[int, Song] = {}
current_message: Dict[int, int] = {}
play_locks: Dict[int, asyncio.Lock] = {}

paused_chats = set()


# ============================================================
# CLIENTS
# ============================================================

bot: Optional[Client] = None
assistant: Optional[Client] = None
voice: Optional[PyTgCalls] = None


# ============================================================
# SEARCH
# ============================================================

async def search_song(query: str) -> List[Song]:
    def do_search():
        return ytmusic.search(
            query,
            filter="songs",
            limit=5,
        )

    results = await asyncio.to_thread(do_search)

    songs = []

    for item in results:
        video_id = item.get("videoId")

        if not video_id:
            continue

        title = item.get(
            "title",
            "Unknown",
        )

        artists = item.get(
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

        duration = item.get(
            "duration",
            "Unknown",
        )

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
# PLAYBACK
# ============================================================

async def play_song(
    chat_id: int,
    song: Song,
) -> bool:

    global voice

    if voice is None:
        logger.error("PyTgCalls is not initialized.")
        return False

    try:
        stream = create_stream(song)

        await voice.play(
            chat_id,
            stream,
        )

        current_song[chat_id] = song
        paused_chats.discard(chat_id)

        logger.info(
            "Playback started: %s",
            song.title,
        )

        return True

    except Exception:
        logger.exception(
            "Playback failed for: %s",
            song.title,
        )

        return False


async def play_next(chat_id: int):
    lock = play_locks.setdefault(
        chat_id,
        asyncio.Lock(),
    )

    async with lock:

        if not queues.get(chat_id):
            current_song.pop(chat_id, None)
            paused_chats.discard(chat_id)
            return

        song = queues[chat_id].pop(0)

        success = await play_song(
            chat_id,
            song,
        )

        if not success:
            await play_next(chat_id)


# ============================================================
# NOW PLAYING CARD
# ============================================================

def now_playing_text(
    song: Song,
) -> str:

    return (
        "╭─────────────────────╮\n"
        "│   🎧 <b>NOW PLAYING</b>   │\n"
        "├─────────────────────┤\n"
        "│                     │\n"
        f"│ 🎵 <b>{song.title}</b>\n"
        f"│ 👤 {song.artist}\n"
        f"│ ⏱ {song.duration}\n"
        "│                     │\n"
        "│ 🎧 <i>Voice Chat</i>\n"
        "╰─────────────────────╯"
    )


def now_playing_keyboard(
    chat_id: int,
):
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
            ],
            [
                InlineKeyboardButton(
                    "⏹ Stop",
                    callback_data=f"stop:{chat_id}",
                ),
                InlineKeyboardButton(
                    "🔗 YouTube",
                    callback_data=f"youtube:{chat_id}",
                ),
            ],
        ]
    )


async def send_now_playing(
    chat_id: int,
):

    if bot is None:
        return

    song = current_song.get(chat_id)

    if song is None:
        return

    text = now_playing_text(song)

    keyboard = now_playing_keyboard(
        chat_id,
    )

    old_message_id = current_message.get(
        chat_id
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
        message = await bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

        current_message[chat_id] = message.id

    except Exception:
        logger.exception(
            "Failed to send now playing message."
        )


# ============================================================
# START COMMAND
# ============================================================

async def start_handler(
    client,
    message,
):

    await message.reply_text(
        "🎵 <b>Zelmo Music Bot</b>\n\n"
        "I can play YouTube Music songs "
        "in Telegram Voice Chat.\n\n"
        "Use:\n"
        "<code>/play song name</code>\n"
        "<code>/queue</code>\n"
        "<code>/now</code>\n"
        "<code>/skip</code>\n"
        "<code>/pause</code>\n"
        "<code>/resume</code>\n"
        "<code>/stop</code>",
        disable_web_page_preview=True,
    )


# ============================================================
# HELP
# ============================================================

async def help_handler(
    client,
    message,
):

    await message.reply_text(
        "🎵 <b>Music Bot Commands</b>\n\n"
        "/play <code>song name</code> - Play song\n"
        "/queue - Show queue\n"
        "/now - Now playing\n"
        "/skip - Skip current song\n"
        "/pause - Pause playback\n"
        "/resume - Resume playback\n"
        "/stop - Stop playback\n"
        "/help - Show help",
    )


# ============================================================
# PLAY
# ============================================================

async def play_handler(
    client,
    message,
):

    if len(message.command) < 2:
        await message.reply_text(
            "❌ Usage:\n"
            "<code>/play song name</code>"
        )
        return

    query = " ".join(
        message.command[1:]
    ).strip()

    status = await message.reply_text(
        f"🔎 Searching for:\n"
        f"<b>{query}</b>"
    )

    try:
        songs = await search_song(
            query
        )

    except Exception as exc:
        logger.exception(
            "YouTube Music search failed."
        )

        await status.edit_text(
            f"❌ Search failed:\n"
            f"<code>{exc}</code>"
        )
        return

    if not songs:
        await status.edit_text(
            "❌ No songs found."
        )
        return

    song = songs[0]

    chat_id = message.chat.id

    queue = queues.setdefault(
        chat_id,
        [],
    )

    if chat_id in current_song:
        queue.append(song)

        await status.edit_text(
            "✅ <b>Added to queue</b>\n\n"
            f"🎵 {song.title}\n"
            f"👤 {song.artist}\n"
            f"⏱ {song.duration}\n\n"
            f"📀 Position: {len(queue)}"
        )

        return

    await status.edit_text(
        f"🎵 <b>Starting playback</b>\n\n"
        f"{song.title}\n"
        f"👤 {song.artist}"
    )

    success = await play_song(
        chat_id,
        song,
    )

    if not success:
        await status.edit_text(
            "❌ Unable to start playback.\n\n"
            "Check yt-dlp and cookies.txt."
        )
        return

    await send_now_playing(
        chat_id
    )


# ============================================================
# QUEUE
# ============================================================

async def queue_handler(
    client,
    message,
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

    lines = [
        "🎵 <b>QUEUE</b>",
        "",
    ]

    for index, song in enumerate(
        queue,
        start=1,
    ):

        lines.append(
            f"{index}. <b>{song.title}</b>\n"
            f"   👤 {song.artist}\n"
            f"   ⏱ {song.duration}"
        )

    await message.reply_text(
        "\n".join(lines),
        disable_web_page_preview=True,
    )


# ============================================================
# NOW
# ============================================================

async def now_handler(
    client,
    message,
):

    chat_id = message.chat.id

    song = current_song.get(
        chat_id
    )

    if song is None:
        await message.reply_text(
            "❌ Nothing is playing."
        )
        return

    await send_now_playing(
        chat_id
    )


# ============================================================
# SKIP
# ============================================================

async def skip_handler(
    client,
    message,
):

    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "❌ Nothing is playing."
        )
        return

    try:
        await voice.stop(
            chat_id
        )
    except Exception:
        pass

    current_song.pop(
        chat_id,
        None,
    )

    await play_next(
        chat_id
    )

    if chat_id in current_song:
        await send_now_playing(
            chat_id
        )
    else:
        await message.reply_text(
            "📭 Queue finished."
        )


# ============================================================
# PAUSE
# ============================================================

async def pause_handler(
    client,
    message,
):

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

        paused_chats.add(
            chat_id
        )

        await message.reply_text(
            "⏸ <b>Playback paused.</b>"
        )

    except Exception as exc:
        logger.exception(
            "Pause failed."
        )

        await message.reply_text(
            f"❌ Pause failed:\n"
            f"<code>{exc}</code>"
        )


# ============================================================
# RESUME
# ============================================================

async def resume_handler(
    client,
    message,
):

    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "❌ Nothing is paused."
        )
        return

    try:
        await voice.resume(
            chat_id
        )

        paused_chats.discard(
            chat_id
        )

        await message.reply_text(
            "▶️ <b>Playback resumed.</b>"
        )

    except Exception as exc:
        logger.exception(
            "Resume failed."
        )

        await message.reply_text(
            f"❌ Resume failed:\n"
            f"<code>{exc}</code>"
        )


# ============================================================
# STOP
# ============================================================

async def stop_handler(
    client,
    message,
):

    chat_id = message.chat.id

    try:
        await voice.leave_group_call(
            chat_id
        )
    except Exception:
        try:
            await voice.stop(
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

    current_message.pop(
        chat_id,
        None,
    )

    paused_chats.discard(
        chat_id
    )

    await message.reply_text(
        "⏹ <b>Playback stopped.</b>"
    )


# ============================================================
# CALLBACKS
# ============================================================

async def callback_handler(
    client,
    callback_query,
):

    data = callback_query.data

    try:
        action, chat_id_text = data.split(
            ":",
            1,
        )

        chat_id = int(
            chat_id_text
        )

    except Exception:
        await callback_query.answer(
            "Invalid button.",
            show_alert=True,
        )
        return

    if action == "pause":

        if chat_id not in current_song:
            await callback_query.answer(
                "Nothing is playing.",
                show_alert=True,
            )
            return

        try:
            await voice.pause(
                chat_id
            )

            paused_chats.add(
                chat_id
            )

            await callback_query.answer(
                "⏸ Paused"
            )

        except Exception as exc:
            logger.exception(
                "Callback pause failed."
            )

            await callback_query.answer(
                str(exc)[:180],
                show_alert=True,
            )

        return

    if action == "prev":

        await callback_query.answer(
            "⏮ Previous is not available yet."
        )

        return

    if action == "skip":

        if chat_id not in current_song:
            await callback_query.answer(
                "Nothing is playing.",
                show_alert=True,
            )
            return

        try:
            await voice.stop(
                chat_id
            )
        except Exception:
            pass

        current_song.pop(
            chat_id,
            None,
        )

        await play_next(
            chat_id
        )

        if chat_id in current_song:
            await send_now_playing(
                chat_id
            )

        await callback_query.answer(
            "⏭ Skipped"
        )

        return

    if action == "stop":

        try:
            await voice.leave_group_call(
                chat_id
            )
        except Exception:
            try:
                await voice.stop(
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

        current_message.pop(
            chat_id,
            None,
        )

        paused_chats.discard(
            chat_id
        )

        await callback_query.answer(
            "⏹ Stopped"
        )

        try:
            await callback_query.message.edit_text(
                "⏹ <b>Playback stopped.</b>"
            )
        except Exception:
            pass

        return

    if action == "youtube":

        song = current_song.get(
            chat_id
        )

        if song is None:
            await callback_query.answer(
                "No current song.",
                show_alert=True,
            )
            return

        await callback_query.answer(
            "Opening YouTube..."
        )

        try:
            await callback_query.message.reply_text(
                f"🔗 <a href=\"{song.youtube_url}\">Open on YouTube</a>"
            )
        except Exception:
            pass

        return

    await callback_query.answer(
        "Unknown action."
    )


# ============================================================
# STREAM END
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

        if chat_id in current_song:
            await send_now_playing(
                chat_id
            )


# ============================================================
# REGISTER HANDLERS
# ============================================================

def register_handlers():

    bot.add_handler(
        MessageHandler(
            start_handler,
            filters.command(
                "start"
            ),
        )
    )

    bot.add_handler(
        MessageHandler(
            help_handler,
            filters.command(
                "help"
            ),
        )
    )

    bot.add_handler(
        MessageHandler(
            play_handler,
            filters.command(
                "play"
            ),
        )
    )

    bot.add_handler(
        MessageHandler(
            queue_handler,
            filters.command(
                "queue"
            ),
        )
    )

    bot.add_handler(
        MessageHandler(
            now_handler,
            filters.command(
                "now"
            ),
        )
    )

    bot.add_handler(
        MessageHandler(
            skip_handler,
            filters.command(
                "skip"
            ),
        )
    )

    bot.add_handler(
        MessageHandler(
            pause_handler,
            filters.command(
                "pause"
            ),
        )
    )

    bot.add_handler(
        MessageHandler(
            resume_handler,
            filters.command(
                "resume"
            ),
        )
    )

    bot.add_handler(
        MessageHandler(
            stop_handler,
            filters.command(
                "stop"
            ),
        )
    )

    bot.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    voice.on_update(
        tg_filters.stream_end()
    )(
        stream_ended_handler
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    global bot
    global assistant
    global voice

    # IMPORTANT:
    # All clients are created inside the same event loop.
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

    register_handlers()

    if os.path.isfile(
        COOKIES_PATH
    ):
        logger.info(
            "Cookies found: %s",
            COOKIES_PATH,
        )
    else:
        logger.warning(
            "Cookies file not found: %s",
            COOKIES_PATH,
        )

    # --------------------------------------------------------
    # START BOT
    # --------------------------------------------------------

    await bot.start()

    me = await bot.get_me()

    logger.info(
        "Bot started: @%s | ID: %s",
        me.username,
        me.id,
    )

    # --------------------------------------------------------
    # START ASSISTANT
    # --------------------------------------------------------

    await assistant.start()

    assistant_me = await assistant.get_me()

    logger.info(
        "Assistant started: @%s | ID: %s",
        assistant_me.username,
        assistant_me.id,
    )

    # --------------------------------------------------------
    # START PYTGCALLS
    # --------------------------------------------------------

    await voice.start()

    logger.info(
        "PyTgCalls started successfully."
    )

    # --------------------------------------------------------
    # STARTUP MESSAGE
    # --------------------------------------------------------

    try:
        await bot.send_message(
            GROUP_ID,
            "🤖 <b>Zelmo Music Bot</b>\n"
            "✅ Bot started successfully.",
        )
    except Exception:
        logger.exception(
            "Could not send bot startup message."
        )

    try:
        await assistant.send_message(
            GROUP_ID,
            "🎧 <b>Zelmo Assistant</b>\n"
            "✅ Assistant connected to Voice Chat system.",
        )
    except Exception:
        logger.exception(
            "Could not send assistant startup message."
        )

    logger.info(
        "MUSIC BOT IS READY"
    )

    # Keep the same event loop alive.
    await asyncio.Event().wait()


# ============================================================
# ENTRY POINT
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

    except Exception:

        logger.exception(
            "Fatal error."
        )
