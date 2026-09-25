"""Har chat (group) ke liye alag song-queue rakhta hai."""

import random
from collections import deque
from typing import Dict, Optional

from music import Track


class ChatQueue:
    def __init__(self):
        self.queue: deque[Track] = deque()
        self.current: Optional[Track] = None
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.loop: bool = False          # current song repeat
        self.volume: int = 100           # 0-200
        self.is_muted: bool = False


class QueueManager:
    def __init__(self):
        self._chats: Dict[int, ChatQueue] = {}

    def get(self, chat_id: int) -> ChatQueue:
        if chat_id not in self._chats:
            self._chats[chat_id] = ChatQueue()
        return self._chats[chat_id]

    def add(self, chat_id: int, track: Track):
        self.get(chat_id).queue.append(track)

    def pop_next(self, chat_id: int) -> Optional[Track]:
        cq = self.get(chat_id)
        if cq.queue:
            return cq.queue.popleft()
        return None

    def add_top(self, chat_id: int, track: Track):
        """Track ko queue ke sabse aage daal do (playnext)."""
        self.get(chat_id).queue.appendleft(track)

    def shuffle(self, chat_id: int):
        cq = self.get(chat_id)
        items = list(cq.queue)
        random.shuffle(items)
        cq.queue = deque(items)

    def clear(self, chat_id: int):
        cq = self.get(chat_id)
        cq.queue.clear()
        cq.current = None
        cq.is_playing = False
        cq.is_paused = False
        cq.loop = False


queue_manager = QueueManager()
