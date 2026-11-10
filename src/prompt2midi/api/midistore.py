"""Generated MIDI files kept in memory for a limited time (TTL) and count."""
from __future__ import annotations

import re
import secrets
import threading
from collections import OrderedDict
from typing import Callable

ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class MidiStore:
    def __init__(self, ttl: float, max_items: int, clock: Callable[[], float]):
        self.ttl, self.max_items, self.clock = ttl, max_items, clock
        self._items: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()
        self._lock = threading.Lock()

    def _purge(self) -> None:
        now = self.clock()
        for k in [k for k, (t, _b) in self._items.items() if now - t >= self.ttl]:
            del self._items[k]
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def put(self, data: bytes) -> str:
        with self._lock:
            self._purge()
            mid = secrets.token_urlsafe(18)
            self._items[mid] = (self.clock(), data)
            self._purge()
            return mid

    def get(self, mid: str) -> bytes | None:
        if not ID_RE.match(mid or ""):
            return None
        with self._lock:
            self._purge()
            item = self._items.get(mid)
            return item[1] if item else None

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._items)
