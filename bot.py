
import asyncio
import logging
import os
import re
import shutil
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from pytgcalls import PyTgCalls, filters as call_filters
from pytgcalls.types import MediaStream, AudioQuality, VideoQuality

import yt_dlp


load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ASSISTANT_SESSION = os.environ["ASSISTANT_SESSION"]
GROUP_ID = int(os.environ["GROUP_ID"])
STORAGE_CHANNEL_ID = int(os.environ["STORAGE_CHANNEL_ID"])

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/var/data/downloads"))
CACHE_SCAN_LIMIT = int(os.getenv("CACHE_SCAN_LIMIT", "1000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("music-bot")

# These are initialized inside main() so the voice client starts
# on the same asyncio loop as the Telegram clients and commands.
bot: Optional[Client] = None
assistant: Optional[Client] = None
voice: Optional[PyTgCalls] = None

queues = defaultdict(deque)
current_tracks = {}
track_locks = defaultdict(asyncio.Lock)


def normalize_query(value: str) -> str:
    """Normalize query text for cache matching."""
    return re.sub(r"\s+", " ", value.strip()).casefold()


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:100] or "track"


def get_track_type(message: Message) -> Optional[str]:
    caption = message.caption or ""
    match = re.search(r"(?im)^Type:\s*(audio|video)\s*$", caption)
    return match.group(1).lower() if match else None


def get_track_query(message: Message) -> Optional[str]:
    caption = message.caption or ""
    match = re.search(r"(?im)^Query:\s*(.+?)\s*$", caption)
    return match.group(1).strip() if match else None


async def find_cached_message(query: str, media_type: str):
    """Find a recent channel message with matching Type and Query metadata."""
    assert bot is not None
    wanted_query = normalize_query(query)

    try:
        async for message in bot.get_chat_history(
            STORAGE_CHANNEL_ID, limit=CACHE_SCAN_LIMIT
        ):
            if not message or not message.media:
                continue

            if get_track_type(message) != media_type:
                continue

            stored_query = get_track_query(message)
            if stored_query and normalize_query(stored_query) == wanted_query:
                return message

    except Exception:
        log.exception("Could not scan storage channel")

    return None


async def download_cached_message(message: Message, query: str) -> str:
    """Download cached Telegram media into the local download directory."""
    assert bot is not None

    prefix = safe_filename(query)
    path = await bot.download_media(
        message,
        file_name=str(DOWNLOAD_DIR / f"cache_{prefix}_"),
    )

    if not path:
        raise RuntimeError("Telegram did not return a downloaded media path")

    return str(path)


def search_download_sync(query: str, media_type: str):
    """Run yt-dlp in a worker thread; returns (file_path, title, video_id)."""
    prefix = safe_filename(query)
    output_template = str(DOWNLOAD_DIR / f"{prefix}_%(id)s.%(ext)s")

    common = {
        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            if media_type == "video"
            else "bestaudio/best"
        ),
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "overwrites": True,
    }

    if media_type == "audio":
        common["format"] = "bestaudio/best"
        common["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        common["merge_output_format"] = "mp4"

    with yt_dlp.YoutubeDL(common) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=True)

    entries = info.get("entries") or []
    if not entries:
        raise RuntimeError("No YouTube result found")

    item = entries[0]
    video_id = item.get("id", "")
    title = item.get("title") or query

    # Find the downloaded file matching this video ID.
    candidates = list(DOWNLOAD_DIR.glob(f"*{video_id}*"))
    candidates = [
        p for p in candidates
        if p.is_file() and p.suffix.lower() not in {".part", ".ytdl"}
    ]

    if not candidates:
        # yt-dlp may have used a postprocessor or a different output extension.
        expected_ext = ".mp3" if media_type == "audio" else ".mp4"
        candidates = [
            p for p in DOWNLOAD_DIR.glob(f"*{prefix}*")
            if p.is_file() and p.suffix.lower() == expected_ext
        ]

    if not candidates:
        raise RuntimeError("Download finished, but the media file was not found")

    path = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(path), title, video_id


async def search_download(query: str, media_type: str):
    return await asyncio.to_thread(search_download_sync, query, media_type)


async def upload_to_storage(
    file_path: str,
    title: str,
    video_id: str,
    query: str,
    media_type: str,
):
    """Upload media and metadata to the configured Telegram storage channel."""
    assert bot is not None

    caption = (
        f"Title: {title}\n"
        f"Video ID: {video_id}\n"
        f"Type: {media_type}\n"
        f"Query: {query}"
    )[:1024]

    if media_type == "audio":
        return await bot.send_audio(
            chat_id=STORAGE_CHANNEL_ID,
            audio=file_path,
            caption=caption,
            title=title[:64],
        )

    return await bot.send_video(
        chat_id=STORAGE_CHANNEL_ID,
        video=file_path,
        caption=caption,
        supports_streaming=True,
    )


async def resolve_track(query: str, media_type: str):
    """Use the Telegram cache first; download from YouTube on a cache miss."""
    cached = await find_cached_message(query, media_type)

    if cached:
        log.info("Cache hit: %s (%s)", query, media_type)
        path = await download_cached_message(cached, query)
        title = (cached.caption or query).splitlines()[0].removeprefix("Title: ")
        return {
            "query": query,
            "title": title,
            "type": media_type,
            "path": path,
            "source_message_id": cached.id,
        }

    log.info("Cache miss; searching YouTube: %s (%s)", query, media_type)
    path, title, video_id = await search_download(query, media_type)

    try:
        await upload_to_storage(path, title, video_id, query, media_type)
        log.info("Uploaded to storage channel: %s", title)
    except Exception:
        # Playback can still proceed even if channel caching fails.
        log.exception("Could not upload media to storage channel")

    return {
        "query": query,
        "title": title,
        "type": media_type,
        "path": path,
        "video_id": video_id,
    }


def make_stream(track):
    if track["type"] == "video":
        return MediaStream(
            track["path"],
            audio_parameters=AudioQuality.HIGH,
            video_parameters=VideoQuality.HD_720p,
        )

    return MediaStream(
        track["path"],
        audio_parameters=AudioQuality.HIGH,
    )


async def start_next_track(chat_id: int):
    """Start the next queued track. Calls are serialized per chat."""
    assert voice is not None

    async with track_locks[chat_id]:
        if not queues[chat_id]:
            current_tracks.pop(chat_id, None)
            return

        track = queues[chat_id].popleft()

        try:
            await voice.play(chat_id, make_stream(track))
            current_tracks[chat_id] = track
            log.info("Now playing in %s: %s", chat_id, track["title"])
        except Exception:
            log.exception("Voice playback failed")
            current_tracks.pop(chat_id, None)

            # Try the next item rather than leaving the queue stuck.
            if queues[chat_id]:
                asyncio.create_task(start_next_track(chat_id))


async def enqueue_track(chat_id: int, track):
    queues[chat_id].append(track)

    if chat_id not in current_tracks:
        await start_next_track(chat_id)


async def on_play_command(_, message: Message):
    if not message.from_user:
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text("Usage: /play song name")
        return

    query = parts[1].strip()
    status = await message.reply_text(f"🔎 Searching: {query}")

    try:
        track = await resolve_track(query, "audio")
        await enqueue_track(GROUP_ID, track)
        await status.edit_text(f"🎵 Added to queue: **{track['title']}**")
    except Exception as exc:
        log.exception("Play command failed")
        await status.edit_text(f"❌ Could not play this track: {exc}")


async def on_vplay_command(_, message: Message):
    if not message.from_user:
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text("Usage: /vplay video name")
        return

    query = parts[1].strip()
    status = await message.reply_text(f"🔎 Searching video: {query}")

    try:
        track = await resolve_track(query, "video")
        await enqueue_track(GROUP_ID, track)
        await status.edit_text(f"📺 Added to queue: **{track['title']}**")
    except Exception as exc:
        log.exception("Video play command failed")
        await status.edit_text(f"❌ Could not play this video: {exc}")


async def on_queue_command(_, message: Message):
    queue = queues[GROUP_ID]
    current = current_tracks.get(GROUP_ID)

    lines = ["**🎶 Music Queue**"]
    if current:
        lines.append(f"▶️ Now: {current['title']} ({current['type']})")
    else:
        lines.append("⏹ Nothing is playing.")

    if queue:
        for index, track in enumerate(list(queue)[:20], start=1):
            lines.append(f"{index}. {track['title']} ({track['type']})")
    else:
        lines.append("Queue is empty.")

    await message.reply_text("\n".join(lines))


async def on_now_command(_, message: Message):
    track = current_tracks.get(GROUP_ID)
    if not track:
        await message.reply_text("Nothing is playing right now.")
        return

    await message.reply_text(
        f"▶️ **Now playing:** {track['title']}\n"
        f"**Type:** {track['type']}\n"
        f"**Query:** {track['query']}"
    )


async def on_skip_command(_, message: Message):
    assert voice is not None

    try:
        await voice.leave_call(GROUP_ID)
    except Exception:
        log.exception("Could not leave current voice call")

    current_tracks.pop(GROUP_ID, None)

    if queues[GROUP_ID]:
        await start_next_track(GROUP_ID)
        await message.reply_text("⏭ Skipped to the next track.")
    else:
        await message.reply_text("⏭ Skipped. The queue is empty.")


async def on_pause_command(_, message: Message):
    assert voice is not None
    try:
        await voice.pause_stream(GROUP_ID)
        await message.reply_text("⏸ Playback paused.")
    except Exception as exc:
        await message.reply_text(f"Could not pause playback: {exc}")


async def on_resume_command(_, message: Message):
    assert voice is not None
    try:
        await voice.resume_stream(GROUP_ID)
        await message.reply_text("▶️ Playback resumed.")
    except Exception as exc:
        await message.reply_text(f"Could not resume playback: {exc}")


async def on_stop_command(_, message: Message):
    assert voice is not None

    queues[GROUP_ID].clear()
    current_tracks.pop(GROUP_ID, None)

    try:
        await voice.leave_call(GROUP_ID)
    except Exception:
        log.exception("Could not leave voice chat")

    await message.reply_text("⏹ Playback stopped and queue cleared.")


async def on_stream_end(_, update):
    """Advance the queue when PyTgCalls reports the stream has ended."""
    try:
        chat_id = update.chat_id
    except AttributeError:
        return

    if chat_id != GROUP_ID:
        return

    current_tracks.pop(chat_id, None)
    if queues[chat_id]:
        await start_next_track(chat_id)


async def main():
    global bot, assistant, voice

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

    # Create PyTgCalls inside the running main event loop.
    voice = PyTgCalls(assistant)

    bot.add_handler(MessageHandler(on_play_command, filters.command("play")))
    bot.add_handler(MessageHandler(on_vplay_command, filters.command("vplay")))
    bot.add_handler(MessageHandler(on_queue_command, filters.command("queue")))
    bot.add_handler(MessageHandler(on_now_command, filters.command("now")))
    bot.add_handler(MessageHandler(on_skip_command, filters.command("skip")))
    bot.add_handler(MessageHandler(on_pause_command, filters.command("pause")))
    bot.add_handler(MessageHandler(on_resume_command, filters.command("resume")))
    bot.add_handler(MessageHandler(on_stop_command, filters.command("stop")))

    voice.on_update(call_filters.stream_end())(on_stream_end)

    try:
        await bot.start()
        await assistant.start()
        await voice.start()

        log.info("Bot, assistant, and voice client started")
        await asyncio.Event().wait()

    finally:
        log.info("Shutting down clients")

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


if __name__ == "__main__":
    asyncio.run(main())
