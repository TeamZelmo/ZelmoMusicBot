
import asyncio
import logging
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yt_dlp
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pytgcalls import PyTgCalls, filters as call_filters
from pytgcalls.types import MediaStream, StreamEnded


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", "").strip()

GROUP_ID = int(os.getenv("GROUP_ID", "0"))
STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID", "0"))

COOKIES_PATH = os.getenv("COOKIES_PATH", "/app/cookies.txt").strip()
DATABASE_PATH = os.getenv(
    "DATABASE_PATH", "/var/data/music_cache.db"
).strip()
DOWNLOAD_DIR = Path(
    os.getenv("DOWNLOAD_DIR", "/var/data/downloads")
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("music-bot")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)

if not API_ID or not API_HASH or not BOT_TOKEN or not ASSISTANT_SESSION:
    raise RuntimeError(
        "Missing API_ID, API_HASH, BOT_TOKEN, or ASSISTANT_SESSION."
    )

if not GROUP_ID or not STORAGE_CHANNEL_ID:
    raise RuntimeError(
        "Set GROUP_ID and STORAGE_CHANNEL_ID in Render environment."
    )


# ---------------------------------------------------------
# TELEGRAM CLIENTS
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

db = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

db.execute(
    """
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
    """
)
db.execute(
    "CREATE INDEX IF NOT EXISTS idx_cache_query ON song_cache(query_key, media_type)"
)
db.commit()

db_lock = asyncio.Lock()


def normalize(value: str) -> str:
    value = value.casefold().strip()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split())


@dataclass
class Song:
    title: str
    youtube_id: str
    media_type: str
    file_path: str
    duration: int = 0
    requested_by: str = "Unknown"


@dataclass
class QueueItem:
    query: str
    media_type: str
    requested_by: str


queues: dict[int, list[QueueItem]] = {}
current_songs: dict[int, Optional[Song]] = {}
play_locks: dict[int, asyncio.Lock] = {}


def get_play_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in play_locks:
        play_locks[chat_id] = asyncio.Lock()
    return play_locks[chat_id]


# ---------------------------------------------------------
# YOUTUBE SEARCH AND DOWNLOAD
# ---------------------------------------------------------

def ytdlp_options(extra: Optional[dict] = None) -> dict:
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

    if COOKIES_PATH and Path(COOKIES_PATH).is_file():
        options["cookiefile"] = COOKIES_PATH

    if extra:
        options.update(extra)

    return options


async def search_youtube(query: str) -> dict:
    def _search():
        options = ytdlp_options({
            "extract_flat": True,
            "skip_download": True,
        })

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

    return await asyncio.to_thread(_search)


async def download_media(url: str, media_type: str) -> str:
    work_dir = Path(
        tempfile.mkdtemp(prefix="music_", dir=str(DOWNLOAD_DIR))
    )

    def _download():
        if media_type == "audio":
            options = ytdlp_options({
                "format": "bestaudio/best",
                "outtmpl": str(work_dir / "%(id)s.%(ext)s"),
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            })
        else:
            options = ytdlp_options({
                "format": (
                    "best[ext=mp4][vcodec!=none][acodec!=none]"
                    "/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"
                ),
                "merge_output_format": "mp4",
                "outtmpl": str(work_dir / "%(id)s.%(ext)s"),
            })

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)

        files = [
            p for p in work_dir.iterdir()
            if p.is_file() and p.stat().st_size > 0
        ]

        if not files:
            raise RuntimeError("Download completed without a media file.")

        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return str(files[0])

    try:
        return await asyncio.to_thread(_download)
    except Exception:
        log.exception("Media download failed")
        raise


# ---------------------------------------------------------
# CACHE LOOKUP
# ---------------------------------------------------------

async def find_cached(query: str, media_type: str):
    query_key = normalize(query)

    async with db_lock:
        row = db.execute(
            """
            SELECT *
            FROM song_cache
            WHERE query_key = ? AND media_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (query_key, media_type),
        ).fetchone()

    if not row:
        return None

    try:
        message = await bot.get_messages(
            STORAGE_CHANNEL_ID,
            int(row["channel_message_id"]),
        )

        if not message or getattr(message, "empty", False):
            return None

        has_expected_media = (
            bool(message.audio or message.document)
            if media_type == "audio"
            else bool(message.video or message.document)
        )

        if not has_expected_media:
            return None

        return row
    except Exception:
        log.exception("Unable to read cached Telegram message")
        return None


async def save_cache(
    query: str,
    title: str,
    youtube_id: str,
    media_type: str,
    message_id: int,
    duration: int,
):
    async with db_lock:
        db.execute(
            """
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
            """,
            (
                normalize(query),
                normalize(title),
                youtube_id,
                media_type,
                message_id,
                title,
                duration,
            ),
        )
        db.commit()


async def download_cached_message(message_id: int) -> str:
    message = await bot.get_messages(STORAGE_CHANNEL_ID, message_id)

    if not message or getattr(message, "empty", False):
        raise RuntimeError("Cached Telegram message no longer exists.")

    path = await bot.download_media(
        message,
        file_name=str(DOWNLOAD_DIR) + os.sep,
    )

    if not path or not Path(path).is_file():
        raise RuntimeError("Could not download cached Telegram media.")

    return str(path)


# ---------------------------------------------------------
# STORAGE CHANNEL
# ---------------------------------------------------------

async def upload_to_storage(
    file_path: str,
    title: str,
    query: str,
    youtube_id: str,
    media_type: str,
    duration: int,
) -> Optional[int]:
    caption = (
        f"{title[:150]}\n"
        f"YTID: {youtube_id}\n"
        f"TYPE: {media_type}\n"
        f"QUERY: {query[:200]}"
    )

    try:
        if media_type == "audio":
            message = await bot.send_audio(
                chat_id=STORAGE_CHANNEL_ID,
                audio=file_path,
                title=title[:64],
                duration=duration,
                caption=caption,
            )
        else:
            message = await bot.send_video(
                chat_id=STORAGE_CHANNEL_ID,
                video=file_path,
                caption=caption,
                supports_streaming=True,
                duration=duration,
            )

        return message.id

    except Exception:
        log.exception("Storage channel upload failed")
        return None


# ---------------------------------------------------------
# GET SONG: CACHE FIRST, YOUTUBE SECOND
# ---------------------------------------------------------

async def get_song(
    query: str,
    media_type: str,
    requested_by: str,
) -> Song:
    # Check the cache before making a YouTube search.
    cached = await find_cached(query, media_type)

    if cached:
        log.info("Cache hit: %s", query)

        path = await download_cached_message(
            int(cached["channel_message_id"])
        )

        return Song(
            title=cached["title"],
            youtube_id=cached["youtube_id"],
            media_type=media_type,
            file_path=path,
            duration=int(cached["duration"] or 0),
            requested_by=requested_by,
        )

    log.info("Cache miss; searching YouTube: %s", query)

    result = await search_youtube(query)

    path = await download_media(result["url"], media_type)

    message_id = await upload_to_storage(
        file_path=path,
        title=result["title"],
        query=query,
        youtube_id=result["id"],
        media_type=media_type,
        duration=result["duration"],
    )

    if message_id is not None:
        await save_cache(
            query=query,
            title=result["title"],
            youtube_id=result["id"],
            media_type=media_type,
            message_id=message_id,
            duration=result["duration"],
        )

    return Song(
        title=result["title"],
        youtube_id=result["id"],
        media_type=media_type,
        file_path=path,
        duration=result["duration"],
        requested_by=requested_by,
    )


# ---------------------------------------------------------
# PLAYBACK AND QUEUE
# ---------------------------------------------------------

async def start_song(chat_id: int, item: QueueItem):
    async with get_play_lock(chat_id):
        song = await get_song(
            item.query,
            item.media_type,
            item.requested_by,
        )

        stream = (
            MediaStream(
                song.file_path,
                video_flags=MediaStream.Flags.IGNORE,
            )
            if song.media_type == "audio"
            else MediaStream(song.file_path)
        )

        await voice.play(chat_id, stream)
        current_songs[chat_id] = song

        try:
            await bot.send_message(
                chat_id,
                f"▶️ **Now Playing**\n"
                f"🎵 {song.title}\n"
                f"🎧 Type: {song.media_type}\n"
                f"👤 Requested by: {song.requested_by}",
            )
        except Exception:
            log.exception("Could not send now-playing message")


async def play_next(chat_id: int):
    async with get_play_lock(chat_id):
        if current_songs.get(chat_id) is not None:
            return

        queue = queues.setdefault(chat_id, [])
        if not queue:
            return

        item = queue.pop(0)

    try:
        await start_song(chat_id, item)
    except Exception as exc:
        log.exception("Playback failed")
        await bot.send_message(
            chat_id,
            f"❌ Playback failed: {str(exc)[:300]}",
        )
        await play_next(chat_id)


async def enqueue(
    chat_id: int,
    query: str,
    media_type: str,
    requested_by: str,
):
    item = QueueItem(
        query=query,
        media_type=media_type,
        requested_by=requested_by,
    )

    if current_songs.get(chat_id) is None:
        await start_song(chat_id, item)
    else:
        queues.setdefault(chat_id, []).append(item)
        await bot.send_message(
            chat_id,
            f"➕ Added to queue: **{query}** "
            f"(position {len(queues[chat_id])})",
        )


async def stream_ended_handler(_, update):
    chat_id = getattr(update, "chat_id", None)

    if chat_id is None:
        return

    current_songs[chat_id] = None
    await play_next(chat_id)


# ---------------------------------------------------------
# COMMANDS
# ---------------------------------------------------------

def requester_name(message) -> str:
    user = message.from_user

    if not user:
        return "Unknown"

    return user.first_name or user.username or str(user.id)


async def handle_play_command(_, message, media_type: str):
    if message.chat.id != GROUP_ID:
        await message.reply_text(
            "❌ Please use this command in the configured music group."
        )
        return

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        command = "/play" if media_type == "audio" else "/vplay"
        await message.reply_text(
            f"Usage: `{command} song name`"
        )
        return

    query = parts[1].strip()

    if len(query) > 300:
        await message.reply_text("❌ Search query is too long.")
        return

    await message.reply_text(f"🔎 Processing: **{query}**")

    try:
        await enqueue(
            GROUP_ID,
            query,
            media_type,
            requester_name(message),
        )
    except Exception as exc:
        log.exception("Command failed")
        await message.reply_text(
            f"❌ Could not process request: {str(exc)[:300]}"
        )


async def play_command(client, message):
    await handle_play_command(client, message, "audio")


async def vplay_command(client, message):
    await handle_play_command(client, message, "video")


async def queue_command(_, message):
    queue = queues.get(message.chat.id, [])
    current = current_songs.get(message.chat.id)

    lines = ["📜 **Music Queue**"]

    if current:
        lines.append(f"▶️ Now: {current.title}")

    if not queue:
        lines.append("Queue is empty.")
    else:
        for index, item in enumerate(queue[:15], start=1):
            lines.append(f"{index}. {item.query} ({item.media_type})")

    await message.reply_text("\n".join(lines))


async def now_command(_, message):
    current = current_songs.get(message.chat.id)

    if not current:
        await message.reply_text("Nothing is playing right now.")
        return

    await message.reply_text(
        f"▶️ **Now Playing**\n"
        f"🎵 {current.title}\n"
        f"🎧 Type: {current.media_type}"
    )


async def skip_command(_, message):
    chat_id = message.chat.id

    if not current_songs.get(chat_id):
        await message.reply_text("Nothing is playing.")
        return

    try:
        await voice.leave_call(chat_id)
    except Exception:
        log.exception("Could not leave current voice call")

    current_songs[chat_id] = None
    await play_next(chat_id)


async def pause_command(_, message):
    try:
        await voice.pause(message.chat.id)
        await message.reply_text("⏸️ Playback paused.")
    except Exception as exc:
        await message.reply_text(f"❌ Pause failed: {str(exc)[:200]}")


async def resume_command(_, message):
    try:
        await voice.resume(message.chat.id)
        await message.reply_text("▶️ Playback resumed.")
    except Exception as exc:
        await message.reply_text(f"❌ Resume failed: {str(exc)[:200]}")


async def stop_command(_, message):
    chat_id = message.chat.id

    queues[chat_id] = []
    current_songs[chat_id] = None

    try:
        await voice.leave_call(chat_id)
    except Exception:
        log.exception("Could not leave voice chat")

    await message.reply_text("⏹️ Playback stopped and queue cleared.")


async def start_command(_, message):
    await message.reply_text(
        "🎵 **Music Bot is running!**\n\n"
        "/play song name - Play audio\n"
        "/vplay video name - Play video\n"
        "/queue - Show queue\n"
        "/now - Current track\n"
        "/skip - Skip current track\n"
        "/pause - Pause playback\n"
        "/resume - Resume playback\n"
        "/stop - Stop and clear queue"
    )


# ---------------------------------------------------------
# REGISTER HANDLERS
# ---------------------------------------------------------

bot.add_handler(
    MessageHandler(start_command, filters.command("start"))
)
bot.add_handler(
    MessageHandler(play_command, filters.command("play"))
)
bot.add_handler(
    MessageHandler(vplay_command, filters.command("vplay"))
)
bot.add_handler(
    MessageHandler(queue_command, filters.command("queue"))
)
bot.add_handler(
    MessageHandler(now_command, filters.command("now"))
)
bot.add_handler(
    MessageHandler(skip_command, filters.command("skip"))
)
bot.add_handler(
    MessageHandler(pause_command, filters.command("pause"))
)
bot.add_handler(
    MessageHandler(resume_command, filters.command("resume"))
)
bot.add_handler(
    MessageHandler(stop_command, filters.command("stop"))
)


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

async def main():
    try:
        await bot.start()
        log.info("Bot client started")

        await assistant.start()
        log.info("Assistant client started")

        await voice.start()
        log.info("Voice client started")

        voice.on_update(
            call_filters.stream_end()
        )(stream_ended_handler)

        await bot.send_message(
            GROUP_ID,
            "✅ Music bot is online. Send /start to see commands.",
        )

        await asyncio.Event().wait()

    finally:
        try:
            await voice.stop()
        except Exception:
            log.exception("Error stopping voice client")

        try:
            await assistant.stop()
        except Exception:
            log.exception("Error stopping assistant")

        try:
            await bot.stop()
        except Exception:
            log.exception("Error stopping bot")

        db.close()


if __name__ == "__main__":
    asyncio.run(main())
