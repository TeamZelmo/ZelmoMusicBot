"""
ytmusicapi -> Song search + metadata (title, artist, duration, videoId, thumbnail)
yt-dlp     -> Actual playable audio stream URL nikalne ke liye (VC me stream karne ke liye)

Reference: https://github.com/sigma67/ytmusicapi
"""

import asyncio
from dataclasses import dataclass
from typing import Optional

from ytmusicapi import YTMusic
import yt_dlp

ytmusic = YTMusic()  # public (anonymous) search ke liye login file ki zaroorat nahi


@dataclass
class Track:
    title: str
    artist: str
    video_id: str
    duration: str
    thumbnail: str
    url: str  # youtube watch url

    @property
    def stream_ready_id(self):
        return self.video_id


def _search_sync(query: str) -> Optional[Track]:
    """ytmusicapi se query search karke sabse best (pehla song) result return karta hai."""
    results = ytmusic.search(query, filter="songs", limit=5)
    if not results:
        # agar "songs" filter me kuch na mile to general search try karo
        results = ytmusic.search(query, limit=5)
    if not results:
        return None

    top = results[0]
    video_id = top.get("videoId")
    if not video_id:
        return None

    title = top.get("title", "Unknown Title")
    artists = top.get("artists") or []
    artist_name = ", ".join(a.get("name", "") for a in artists) or "Unknown Artist"

    duration = top.get("duration") or "N/A"

    thumbnails = top.get("thumbnails") or []
    thumbnail = thumbnails[-1]["url"] if thumbnails else ""

    return Track(
        title=title,
        artist=artist_name,
        video_id=video_id,
        duration=duration,
        thumbnail=thumbnail,
        url=f"https://music.youtube.com/watch?v={video_id}",
    )


async def search_track(query: str) -> Optional[Track]:
    """Async wrapper - ytmusicapi sync hai isliye thread me chalate hain."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _search_sync, query)


def _get_stream_url_sync(video_id: str) -> Optional[str]:
    """yt-dlp se best audio-only direct stream URL nikalta hai (koi file download nahi hoti)."""
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "extract_flat": False,
    }
    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("url")


async def get_stream_url(video_id: str) -> Optional[str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_stream_url_sync, video_id)


def _get_lyrics_sync(video_id: str) -> Optional[str]:
    """ytmusicapi se lyrics nikalta hai (agar available ho)."""
    try:
        watch_playlist = ytmusic.get_watch_playlist(video_id)
        lyrics_browse_id = watch_playlist.get("lyrics")
        if not lyrics_browse_id:
            return None
        lyrics_data = ytmusic.get_lyrics(lyrics_browse_id)
        return lyrics_data.get("lyrics")
    except Exception:
        return None


async def get_lyrics(video_id: str) -> Optional[str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_lyrics_sync, video_id)
