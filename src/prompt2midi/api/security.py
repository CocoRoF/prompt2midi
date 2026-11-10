"""Password hashing (scrypt), admin sessions + CSRF tokens, sliding-window rate limits."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
from collections import defaultdict, deque
from typing import Callable

MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_CHARS = 256
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> dict:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return {"algo": "scrypt", **{k: v for k, v in _SCRYPT.items()}, "salt": base64.b64encode(salt).decode("ascii"), "hash": base64.b64encode(h).decode("ascii")}


def verify_password(password: str, record: dict) -> bool:
    try:
        if record.get("algo") != "scrypt":
            return False
        params = {k: int(record[k]) for k in ("n", "r", "p", "dklen")}
        h = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(record["salt"]), **params)
        return hmac.compare_digest(h, base64.b64decode(record["hash"]))
    except (KeyError, ValueError, TypeError):
        return False


class Sessions:
    """In-memory sessions (a restart logs everyone out). Each has its own CSRF token."""

    def __init__(self, ttl: float, clock: Callable[[], float]):
        self.ttl, self.clock = ttl, clock
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self._lock:
            self._purge()
            self._items[token] = {"csrf": csrf, "created": self.clock()}
        return token, csrf

    def get(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self._lock:
            item = self._items.get(token)
            if item is None:
                return None
            if self.clock() - item["created"] >= self.ttl:
                del self._items[token]
                return None
            return item

    def delete(self, token: str | None) -> None:
        with self._lock:
            self._items.pop(token or "", None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _purge(self) -> None:
        now = self.clock()
        for t in [t for t, i in self._items.items() if now - i["created"] >= self.ttl]:
            del self._items[t]


class SlidingWindow:
    """At most `limit` events per `window` seconds per key."""

    def __init__(self, window: float, clock: Callable[[], float]):
        self.window, self.clock = window, clock
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q: deque) -> None:
        cutoff = self.clock() - self.window
        while q and q[0] <= cutoff:
            q.popleft()

    def allow_and_record(self, key: str, limit: int) -> bool:
        with self._lock:
            q = self._events[key]
            self._trim(q)
            if len(q) >= limit:
                return False
            q.append(self.clock())
            return True

    def count(self, key: str) -> int:
        with self._lock:
            q = self._events[key]
            self._trim(q)
            return len(q)

    def record(self, key: str) -> None:
        with self._lock:
            self._events[key].append(self.clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
