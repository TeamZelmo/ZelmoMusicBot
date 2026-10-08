import os
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List

from dotenv import load_dotenv

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardMarimport
import re
import json
import asyncio
import logging
import sqlite3
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import yt_dlp
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.errors import MessageIdInvalid
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message
from pytgcalls import PyTgCalls, filters as tg_filters
from pytgcalls.types import MediaStream
from pytgcalls.types import StreamEnded

load_dotenv()

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", "").strip()

GROUP_ID = int(os.getenv("GROUP_ID", "0"))
STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID", "0"))
COOKIES_PATH = os.getenv("COOKIES_PATH", "/app/cookies.txt")

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise RuntimeError("API_ID, API_HASH and BOT_TOKEN are required.")

if not ASSISTANT_SESSION:
    raise RuntimeError("ASSISTANT_SESSION is required.")

if not GROUP_ID or not STORAGE_CHANNEL_ID:
    raise RuntimeError("GROUP_ID and STORAGE_CHANNEL_ID are required.")

# --------------------------------------------------
# LOGGING
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("MusicBot")

# --------------------------------------------------
# CLIENTS
# --------------------------------------------------

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

# --------------------------------------------------
# DATABASE
# --------------------------------------------------

DB_PATH = os.getenv("DATABASE_PATH", "/app/music_cache.db")

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS song_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_key TEXT NOT NULL,
    title_key TEXT NOT NULL,
    youtube_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    channel_message_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    duration INTEGER DEFAULT 0,
    UNIQUE(youtube_id, media_type)
)
""")

db.execute("""
CREATE INDEX IF NOT EXISTS idx_cache_query
ON song_cache(query_key, media_type)
""")

db.commit()

db_lock = asyncio.Lock()

# --------------------------------------------------
# PLAYBACK STATE
# --------------------------------------------------

@dataclass
class Song:
    title: str
    youtube_url: str
    youtube_id: str
    duration: int
    media_type: str
    query_key: str
    file_path: Optional[str] = None
    channel_message_id: Optional[int] = None


queues = {}
current_song = {}
paused_chats = set()
play_locks = {}
download_locks = {}
current_message = {}

DOWNLOAD_DIR = Path("/app/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def normalize(text: str) -> str:
    text = text.casefold().strip()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in play_locks:
        play_locks[chat_id] = asyncio.Lock()
    return play_locks[chat_id]


def get_download_lock(key: str) -> asyncio.Lock:
    if key not in download_locks:
        download_locks[key] = asyncio.Lock()
    return download_locks[key]


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02}:{secs:02}"

    return f"{minutes}:{secs:02}"


# --------------------------------------------------
# YOUTUBE SEARCH
# --------------------------------------------------

def ytdlp_options() -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["default", "web_embedded"]
            }
        },
    }

    if os.path.isfile(COOKIES_PATH):
        options["cookiefile"] = COOKIES_PATH

    return options


async def search_youtube(query: str) -> Song:
    logger.info("YouTube search: %s", query)

    options = ytdlp_options()
    options.update({
        "extract_flat": True,
        "skip_download": True,
    })

    def search():
        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.extract_info(
                f"ytsearch1:{query}",
                download=False,
            )

            entries = result.get("entries") or []
            if not entries:
                raise RuntimeError("No YouTube results found.")

            item = entries[0]
            video_id = item.get("id")

            if not video_id:
                raise RuntimeError("YouTube did not return a video ID.")

            return {
                "id": video_id,
                "title": item.get("title") or query,
                "duration": int(item.get("duration") or 0),
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }

    result = await asyncio.to_thread(search)

    return Song(
        title=result["title"],
        youtube_url=result["url"],
        youtube_id=result["id"],
        duration=result["duration"],
        media_type="",
        query_key=normalize(query),
    )


# --------------------------------------------------
# CHANNEL CACHE LOOKUP
# --------------------------------------------------

async def find_cached_song(
    query: str,
    media_type: str,
) -> Optional[dict]:
    """
    Search the local index of media already saved in the
    Telegram storage channel. No YouTube search is run here.
    """
    key = normalize(query)

    async with db_lock:
        row = db.execute("""
            SELECT *
            FROM song_cache
            WHERE media_type = ?
              AND (query_key = ? OR title_key = ?)
            ORDER BY id DESC
            LIMIT 1
        """, (media_type, key, key)).fetchone()

    if not row:
        return None

    # Verify that the indexed message still exists in the channel.
    try:
        message = await bot.get_messages(
            STORAGE_CHANNEL_ID,
            int(row["channel_message_id"]),
        )

        if not message or message.empty or not (
            message.audio or message.video or message.document
        ):
            logger.warning("Cache entry points to missing media.")
            return None

    except Exception:
        logger.exception("Could not verify cached channel message.")
        return None

    return dict(row)


async def save_cache(
    song: Song,
    media_type: str,
    message_id: int,
    original_query: str,
):
    async with db_lock:
        db.execute("""
            INSERT INTO song_cache (
                query_key,
                title_key,
                youtube_id,
                media_type,
                channel_message_id,
                title,
                duration
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(youtube_id, media_type)
            DO UPDATE SET
                query_key = excluded.query_key,
                title_key = excluded.title_key,
                channel_message_id = excluded.channel_message_id,
                title = excluded.title,
                duration = excluded.duration
        """, (
            normalize(original_query),
            normalize(song.title),
            song.youtube_id,
            media_type,
            message_id,
            song.title,
            song.duration,
        ))
        db.commit()


# --------------------------------------------------
# DOWNLOAD MEDIA
# --------------------------------------------------

async def download_song(song: Song, media_type: str) -> str:
    """
    Download one audio or video file into a temporary folder.
    Returns the actual file path produced by yt-dlp.
    """
    temp_dir = tempfile.mkdtemp(
        prefix=f"{media_type}_",
        dir=str(DOWNLOAD_DIR),
    )

    output_template = str(Path(temp_dir) / "%(id)s.%(ext)s")

    options = ytdlp_options()
    options.update({
        "outtmpl": output_template,
        "noplaylist": True,
        "overwrites": False,
        "continuedl": True,
    })

    if media_type == "audio":
        options.update({
            "format": "bestaudio[ext=m4a]/bestaudio/best",
        })
    else:
        options.update({
            "format": "best[ext=mp4][vcodec!=none][acodec!=none]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
            "merge_output_format": "mp4",
        })

    logger.info("Downloading %s: %s", media_type, song.title)

    def download():
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                song.youtube_url,
                download=True,
            )
            filepath = ydl.prepare_filename(info)

            # Video may be merged to MP4 after downloading.
            if media_type == "video":
                merged = str(Path(filepath).with_suffix(".mp4"))
                if os.path.isfile(merged):
                    return merged

            if os.path.isfile(filepath):
                return filepath

            # Find the real output if yt-dlp changed the extension.
            candidates = list(Path(temp_dir).glob(f"{song.youtube_id}.*"))
            if candidates:
                return str(candidates[0])

            raise RuntimeError("Downloaded media file was not found.")

    try:
        path = await asyncio.to_thread(download)

        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError("Downloaded file is missing or empty.")

        song.file_path = path
        logger.info("Download complete: %s", path)
        return path

    except Exception:
        logger.exception("Download failed.")
        raise


# --------------------------------------------------
# UPLOAD TO STORAGE CHANNEL
# --------------------------------------------------

async def upload_to_channel(
    song: Song,
    file_path: str,
    media_type: str,
) -> int:
    caption = (
        f"🎵 {song.title}\n"
        f"ID: {song.youtube_id}\n"
        f"Type: {media_type}"
    )

    if media_type == "audio":
        message = await bot.send_audio(
            STORAGE_CHANNEL_ID,
            audio=file_path,
            title=song.title[:64],
            duration=song.duration,
            caption=caption,
        )
    else:
        message = await bot.send_video(
            STORAGE_CHANNEL_ID,
            video=file_path,
            caption=caption,
            duration=song.duration,
            supports_streaming=True,
        )

    logger.info(
        "Saved in storage channel: message_id=%s",
        message.id,
    )

    return message.id


# --------------------------------------------------
# GET MEDIA: CACHE FIRST, YOUTUBE ONLY IF NEEDED
# --------------------------------------------------

async def get_song(
    query: str,
    media_type: str,
) -> Song:
    """
    1. Check channel cache index.
    2. If found, download that Telegram message locally.
    3. Otherwise search YouTube, download, upload to channel,
       and index the new message.
    """
    key = normalize(query)
    lock = get_download_lock(f"{media_type}:{key}")

    async with lock:
        cached = await find_cached_song(query, media_type)

        if cached:
            logger.info(
                "CACHE HIT: %s (%s); skipping YouTube search.",
                cached["title"],
                media_type,
            )

            message = await bot.get_messages(
                STORAGE_CHANNEL_ID,
                int(cached["channel_message_id"]),
            )

            local_path = await bot.download_media(
                message,
                file_name=str(DOWNLOAD_DIR) + "/",
            )

            if not local_path or not os.path.isfile(local_path):
                raise RuntimeError(
                    "Could not download cached media from the channel."
                )

            return Song(
                title=cached["title"],
                youtube_url=(
                    f"https://www.youtube.com/watch?v={cached['youtube_id']}"
                ),
                youtube_id=cached["youtube_id"],
                duration=int(cached["duration"] or 0),
                media_type=media_type,
                query_key=key,
                file_path=local_path,
                channel_message_id=int(cached["channel_message_id"]),
            )

        logger.info(
            "CACHE MISS: %s (%s); searching YouTube now.",
            query,
            media_type,
        )

        song = await search_youtube(query)
        song.media_type = media_type

        file_path = await download_song(song, media_type)

        # Save the downloaded file in the channel before playback.
        message_id = await upload_to_channel(
            song,
            file_path,
            media_type,
        )

        song.channel_message_id = message_id

        await save_cache(
            song,
            media_type,
            message_id,
            query,
        )

        return song


# --------------------------------------------------
# PLAYBACK
# --------------------------------------------------

async def play_song(chat_id: int, song: Song):
    if not song.file_path:
        raise RuntimeError("Song file path is missing.")

    lock = get_lock(chat_id)

    async with lock:
        current_song[chat_id] = song
        paused_chats.discard(chat_id)

        logger.info(
            "Starting %s playback: %s",
            song.media_type,
            song.title,
        )

        if song.media_type == "audio":
            stream = MediaStream(
                song.file_path,
                video_flags=MediaStream.Flags.IGNORE,
            )
        else:
            stream = MediaStream(song.file_path)

        await voice.play(chat_id, stream)

        await send_now_playing(chat_id, song)


async def send_now_playing(chat_id: int, song: Song):
    old_id = current_message.get(chat_id)

    if old_id:
        try:
            await bot.delete_messages(chat_id, old_id)
        except Exception:
            pass

    mode = "🎧 Audio" if song.media_type == "audio" else "📺 Video"

    message = await bot.send_message(
        chat_id,
        (
            f"🎶 **Now Playing**\n\n"
            f"**{song.title}**\n"
            f"⏱ `{format_duration(song.duration)}`\n"
            f"{mode}\n\n"
            f"Use `/queue`, `/skip`, `/pause`, `/resume`, or `/stop`."
        ),
    )

    current_message[chat_id] = message.id


async def play_next(chat_id: int):
    queue = queues.get(chat_id, [])

    if not queue:
        current_song.pop(chat_id, None)
        return

    song = queue.pop(0)

    try:
        await play_song(chat_id, song)
    except Exception as exc:
        logger.exception("Could not play queued song.")
        await bot.send_message(
            chat_id,
            f"❌ Could not play **{song.title}**: `{str(exc)[:500]}`",
        )
        await play_next(chat_id)


# --------------------------------------------------
# COMMANDS
# --------------------------------------------------

async def start_command(_: Client, message: Message):
    await message.reply_text(
        "🎵 **Music Bot Ready**\n\n"
        "`/play song name` — Audio playback\n"
        "`/vplay song name` — Video playback\n"
        "`/queue` — Show queue\n"
        "`/now` — Current song\n"
        "`/skip` — Skip\n"
        "`/pause` — Pause\n"
        "`/resume` — Resume\n"
        "`/stop` — Stop playback"
    )


async def play_command(_: Client, message: Message):
    await handle_play(message, "audio")


async def vplay_command(_: Client, message: Message):
    await handle_play(message, "video")


async def handle_play(message: Message, media_type: str):
    query = " ".join(message.command[1:]).strip()

    if not query:
        command = "/play" if media_type == "audio" else "/vplay"
        await message.reply_text(
            f"Usage: `{command} song name`"
        )
        return

    status = await message.reply_text(
        "🔎 Checking storage channel first..."
    )

    try:
        song = await get_song(query, media_type)

        chat_id = message.chat.id

        if chat_id in current_song:
            queues.setdefault(chat_id, []).append(song)
            await status.edit_text(
                f"➕ Added to queue: **{song.title}**"
            )
            return

        await status.edit_text(
            f"▶️ Starting: **{song.title}**"
        )

        await play_song(chat_id, song)

        try:
            await status.delete()
        except Exception:
            pass

    except Exception as exc:
        logger.exception("Play request failed.")
        await status.edit_text(
            f"❌ **Playback failed**\n\n`{str(exc)[:700]}`"
        )


async def queue_command(_: Client, message: Message):
    chat_id = message.chat.id
    items = queues.get(chat_id, [])

    if not items:
        await message.reply_text("📭 Queue is empty.")
        return

    lines = ["📜 **Upcoming Queue**"]
    for index, song in enumerate(items[:20], start=1):
        lines.append(
            f"{index}. {song.title} "
            f"({song.media_type}, {format_duration(song.duration)})"
        )

    await message.reply_text("\n".join(lines))


async def now_command(_: Client, message: Message):
    song = current_song.get(message.chat.id)

    if not song:
        await message.reply_text("Nothing is playing right now.")
        return

    await message.reply_text(
        f"🎶 **Now Playing**\n\n"
        f"**{song.title}**\n"
        f"⏱ `{format_duration(song.duration)}`\n"
        f"Mode: `{song.media_type}`"
    )


async def skip_command(_: Client, message: Message):
    chat_id = message.chat.id

    try:
        await voice.leave_call(chat_id)
    except Exception:
        pass

    current_song.pop(chat_id, None)
    paused_chats.discard(chat_id)

    await message.reply_text("⏭ Skipped.")

    await play_next(chat_id)


async def pause_command(_: Client, message: Message):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text("Nothing is playing.")
        return

    try:
        await voice.pause_stream(chat_id)
        paused_chats.add(chat_id)
        await message.reply_text("⏸ Paused.")
    except Exception as exc:
        await message.reply_text(f"❌ Pause failed: `{str(exc)[:300]}`")


async def resume_command(_: Client, message: Message):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text("Nothing is playing.")
        return

    try:
        await voice.resume_stream(chat_id)
        paused_chats.discard(chat_id)
        await message.reply_text("▶️ Resumed.")
    except Exception as exc:
        await message.reply_text(f"❌ Resume failed: `{str(exc)[:300]}`")


async def stop_command(_: Client, message: Message):
    chat_id = message.chat.id

    queues.pop(chat_id, None)
    current_song.pop(chat_id, None)
    paused_chats.discard(chat_id)

    try:
        await voice.leave_call(chat_id)
    except Exception:
        pass

    await message.reply_text("⏹ Playback stopped and queue cleared.")


# --------------------------------------------------
# STREAM END EVENT
# --------------------------------------------------

async def stream_ended_handler(
    _: PyTgCalls,
    update: StreamEnded,
):
    chat_id = update.chat_id

    current_song.pop(chat_id, None)
    paused_chats.discard(chat_id)

    await asyncio.sleep(0.5)

    if queues.get(chat_id):
        await play_next(chat_id)


# --------------------------------------------------
# STARTUP
# --------------------------------------------------

def register_handlers():
    bot.add_handler(MessageHandler(start_command, filters.command("start")))
    bot.add_handler(MessageHandler(play_command, filters.command("play")))
    bot.add_handler(MessageHandler(vplay_command, filters.command("vplay")))
    bot.add_handler(MessageHandler(queue_command, filters.command("queue")))
    bot.add_handler(MessageHandler(now_command, filters.command("now")))
    bot.add_handler(MessageHandler(skip_command, filters.command("skip")))
    bot.add_handler(MessageHandler(pause_command, filters.command("pause")))
    bot.add_handler(MessageHandler(resume_command, filters.command("resume")))
    bot.add_handler(MessageHandler(stop_command, filters.command("stop")))


async def main():
    register_handlers()

    await bot.start()
    logger.info("Bot started.")

    await assistant.start()
    logger.info("Assistant started.")

    await voice.start()
    logger.info("PyTgCalls started.")

    voice.on_update(
        tg_filters.stream_end()
    )(stream_ended_handler)

    try:
        await bot.send_message(
            GROUP_ID,
            "✅ **Music Bot is ready!**\nUse `/play` or `/vplay`.",
        )
    except Exception:
        logger.exception("Could not send startup message.")

    logger.info("MUSIC BOT IS READY")

    try:
        await asyncio.Event().wait()
    finally:
        try:
            await voice.stop()
        except Exception:
            pass

        await assistant.stop()
        await bot.stop()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())kup, InlineKeyboardButton

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
# DATA MODELS
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


# ============================================================
# STATE
# ============================================================

queues: Dict[int, List[Song]] = {}
current_song: Dict[int, Song] = {}
current_message: Dict[int, int] = {}

play_locks: Dict[int, asyncio.Lock] = {}

paused_chats = set()


# ============================================================
# UTILITY
# ============================================================

def get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in play_locks:
        play_locks[chat_id] = asyncio.Lock()

    return play_locks[chat_id]


def format_duration(value: str) -> str:
    if not value:
        return "Unknown"

    value = value.strip()

    if value.lower() in (
        "none",
        "nan",
        "unknown",
    ):
        return "Unknown"

    return value


# ============================================================
# YT-DLP SEARCH
# ============================================================

async def search_song(query: str) -> List[Song]:
    logger.info(
        "yt-dlp search: %s",
        query,
    )

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
        command[1:1] = [
            "--cookies",
            COOKIES_PATH,
        ]

    logger.info(
        "Running yt-dlp search..."
    )

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
            error_output or
            "yt-dlp search failed."
        )

    songs: List[Song] = []

    for line in output.splitlines():
        parts = line.split(
            "\t",
            3,
        )

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

        url = (
            "https://www.youtube.com/watch?v="
            + video_id
        )

        songs.append(
            Song(
                title=title,
                artist=artist,
                url=url,
                duration=format_duration(
                    duration
                ),
            )
        )

    logger.info(
        "yt-dlp search returned %s result(s).",
        len(songs),
    )

    return songs


# ============================================================
# YT-DLP DIRECT AUDIO EXTRACTION
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

        "--extractor-args",
        "youtube:player_client=default,web_embedded",

        song.youtube_url,
    ]

    # --------------------------------------------------------
    # Cookies
    # --------------------------------------------------------

    if os.path.isfile(COOKIES_PATH):
        command[1:1] = [
            "--cookies",
            COOKIES_PATH,
        ]

        logger.info(
            "Using cookies file: %s",
            COOKIES_PATH,
        )
    else:
        logger.warning(
            "Cookies file not found: %s",
            COOKIES_PATH,
        )

    logger.info(
        "Running yt-dlp audio extraction..."
    )

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

    # --------------------------------------------------------
    # Extraction failed
    # --------------------------------------------------------

    if process.returncode != 0:
        logger.error(
            "yt-dlp audio extraction failed:"
        )

        logger.error(
            "%s",
            error_output or "Unknown yt-dlp error",
        )

        raise RuntimeError(
            error_output or
            "yt-dlp failed to extract audio URL."
        )

    # --------------------------------------------------------
    # Empty URL
    # --------------------------------------------------------

    if not audio_url:
        logger.error(
            "yt-dlp returned an empty audio URL."
        )

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

def now_keyboard(
    chat_id: int,
) -> InlineKeyboardMarkup:

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

async def send_now_playing(
    chat_id: int,
):
    song = current_song.get(chat_id)

    if not song:
        return

    # Delete previous now-playing card
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

    text = (
        "╭─────────────────────────╮\n"
        "│     🎧 **NOW PLAYING**     │\n"
        "├─────────────────────────┤\n"
        "│\n"
        f"│ 🎵 **{song.title}**\n"
        "│\n"
        f"│ 👤 **{song.artist}**\n"
        f"│ ⏱ **{song.duration}**\n"
        "│\n"
        "│ 🎙 **Voice Chat**\n"
        "╰─────────────────────────╯"
    )

    message = await bot.send_message(
        chat_id,
        text,
        reply_markup=now_keyboard(
            chat_id
        ),
        disable_web_page_preview=True,
    )

    current_message[
        chat_id
    ] = message.id


# ============================================================
# PLAY SONG
# ============================================================

async def play_song(
    chat_id: int,
    song: Song,
):
    lock = get_lock(
        chat_id
    )

    async with lock:
        try:
            current_song[
                chat_id
            ] = song

            paused_chats.discard(
                chat_id
            )

            logger.info(
                "Preparing playback: %s",
                song.title,
            )

            # ------------------------------------------------
            # STEP 1:
            # YouTube URL -> direct audio URL
            # ------------------------------------------------

            audio_url = await get_audio_url(
                song
            )

            logger.info(
                "Direct URL ready."
            )

            # ------------------------------------------------
            # STEP 2:
            # Direct audio URL -> Telegram VC
            # ------------------------------------------------

            logger.info(
                "Calling PyTgCalls.play()..."
            )

            await voice.play(
                chat_id,
                MediaStream(
                    audio_url
                ),
            )

            logger.info(
                "Playback started: %s",
                song.title,
            )

            await send_now_playing(
                chat_id
            )

        except Exception as exc:
            logger.exception(
                "Playback failed: %s",
                song.title,
            )

            current_song.pop(
                chat_id,
                None,
            )

            paused_chats.discard(
                chat_id
            )

            try:
                await bot.send_message(
                    chat_id,
                    (
                        "❌ **Playback Failed**\n\n"
                        f"🎵 **{song.title}**\n\n"
                        f"⚠️ `{str(exc)[:800]}`"
                    ),
                )
            except Exception:
                pass


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(
    chat_id: int,
):
    queue = queues.get(
        chat_id,
        [],
    )

    if not queue:
        current_song.pop(
            chat_id,
            None,
        )

        paused_chats.discard(
            chat_id
        )

        return

    song = queue.pop(
        0
    )

    await play_song(
        chat_id,
        song,
    )


# ============================================================
# /START
# ============================================================

async def start_command(
    client,
    message,
):
    text = (
        "🎧 **Welcome to Music Bot!**\n\n"
        "YouTube music directly in Telegram Voice Chat.\n\n"
        "🎵 `/play song name`\n"
        "📜 `/queue`\n"
        "🎧 `/now`\n"
        "⏭ `/skip`\n"
        "⏸ `/pause`\n"
        "▶️ `/resume`\n"
        "⏹ `/stop`\n"
        "ℹ️ `/help`"
    )

    await message.reply_text(
        text
    )


# ============================================================
# /HELP
# ============================================================

async def help_command(
    client,
    message,
):
    text = (
        "🎧 **Music Bot Commands**\n\n"
        "🎵 `/play <song>` — Search & play\n"
        "📜 `/queue` — Show queue\n"
        "🎧 `/now` — Current song\n"
        "⏭ `/skip` — Skip song\n"
        "⏸ `/pause` — Pause\n"
        "▶️ `/resume` — Resume\n"
        "⏹ `/stop` — Stop & clear queue"
    )

    await message.reply_text(
        text
    )


# ============================================================
# /PLAY
# ============================================================

async def play_command(
    client,
    message,
):
    if len(message.command) < 2:
        await message.reply_text(
            "❌ **Usage:**\n"
            "`/play song name`"
        )
        return

    query = " ".join(
        message.command[1:]
    ).strip()

    chat_id = message.chat.id

    searching = await message.reply_text(
        f"🔎 **Searching YouTube...**\n\n"
        f"`{query}`"
    )

    try:
        songs = await search_song(
            query
        )

        if not songs:
            await searching.edit_text(
                "❌ **No results found.**"
            )
            return

        song = songs[0]

        if chat_id not in queues:
            queues[
                chat_id
            ] = []

        # ----------------------------------------------------
        # Nothing playing
        # ----------------------------------------------------

        if chat_id not in current_song:

            await searching.edit_text(
                "🎵 **Found!**\n\n"
                f"**{song.title}**\n"
                f"👤 {song.artist}\n"
                f"⏱ {song.duration}\n\n"
                "⏳ **Preparing Voice Chat...**"
            )

            await play_song(
                chat_id,
                song,
            )

        # ----------------------------------------------------
        # Already playing -> queue
        # ----------------------------------------------------

        else:
            queues[
                chat_id
            ].append(song)

            position = len(
                queues[
                    chat_id
                ]
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

async def queue_command(
    client,
    message,
):
    chat_id = message.chat.id

    song = current_song.get(
        chat_id
    )

    queue = queues.get(
        chat_id,
        [],
    )

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
                f"➕ {len(queue) - 10} more..."
            )

    await message.reply_text(
        text
    )


# ============================================================
# /NOW
# ============================================================

async def now_command(
    client,
    message,
):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "❌ **Nothing is playing right now.**"
        )
        return

    await send_now_playing(
        chat_id
    )


# ============================================================
# /SKIP
# ============================================================

async def skip_command(
    client,
    message,
):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "❌ **Nothing is playing.**"
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

    paused_chats.discard(
        chat_id
    )

    if queues.get(chat_id):
        await message.reply_text(
            "⏭ **Skipped. Playing next...**"
        )

        await play_next(
            chat_id
        )

    else:
        await message.reply_text(
            "⏭ **Skipped. Queue is empty.**"
        )


# ============================================================
# /PAUSE
# ============================================================

async def pause_command(
    client,
    message,
):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "❌ **Nothing is playing.**"
        )
        return

    if chat_id in paused_chats:
        await message.reply_text(
            "⏸ **Already paused.**"
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
            "⏸ **Playback paused.**"
        )

    except Exception as exc:
        logger.exception(
            "Pause failed."
        )

        await message.reply_text(
            "❌ **Pause failed**\n\n"
            f"`{str(exc)[:500]}`"
        )


# ============================================================
# /RESUME
# ============================================================

async def resume_command(
    client,
    message,
):
    chat_id = message.chat.id

    if chat_id not in current_song:
        await message.reply_text(
            "❌ **Nothing is playing.**"
        )
        return

    if chat_id not in paused_chats:
        await message.reply_text(
            "▶️ **Already playing.**"
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
            "▶️ **Playback resumed.**"
        )

    except Exception as exc:
        logger.exception(
            "Resume failed."
        )

        await message.reply_text(
            "❌ **Resume failed**\n\n"
            f"`{str(exc)[:500]}`"
        )


# ============================================================
# /STOP
# ============================================================

async def stop_command(
    client,
    message,
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

    await message.reply_text(
        "⏹ **Stopped.**\n\n"
        "🗑 Queue cleared."
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(
    client,
    callback_query,
):
    data = callback_query.data or ""

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
                "Already paused."
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
                "Already playing."
            )
            return

        try:
            await voice.resume(
                chat_id
            )

            paused_chats.discard(
                chat_id
            )

            await callback_query.answer(
                "▶️ Resumed"
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
            "⏭ Skipping..."
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
            "⏹ Stopped"
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
                "🔗 **YouTube Link**\n\n"
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
# STREAM END HANDLER
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

    await asyncio.sleep(
        1
    )

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
# HANDLERS
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

    # --------------------------------------------------------
    # BOT
    # --------------------------------------------------------

    await bot.start()

    logger.info(
        "Bot started: @%s | ID: %s",
        bot.me.username,
        bot.me.id,
    )

    # --------------------------------------------------------
    # ASSISTANT
    # --------------------------------------------------------

    await assistant.start()

    logger.info(
        "Assistant started: @%s | ID: %s",
        assistant.me.username,
        assistant.me.id,
    )

    # --------------------------------------------------------
    # PYTGCALLS
    # --------------------------------------------------------

    await voice.start()

    logger.info(
        "PyTgCalls started successfully."
    )

  
    voice.on_update(
        tg_filters.stream_end()
    )(
        stream_ended_handler
    )


    try:
        await bot.send_message(
            GROUP_ID,
            "🤖 **Music Bot Started Successfully!**\n\n"
            "🎧 Ready for Voice Chat playback.",
        )
    except Exception:
        logger.exception(
            "Bot startup message failed."
        )

    try:
        await assistant.send_message(
            GROUP_ID,
            "🎙 **Assistant is Online!**\n\n"
            "Voice Chat playback is ready.",
        )
    except Exception:
        logger.exception(
            "Assistant startup message failed."
        )

    logger.info(
        "MUSIC BOT IS READY"
    )

    # Keep process alive.
    await asyncio.Event().wait()


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
