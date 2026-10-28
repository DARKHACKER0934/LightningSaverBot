# devggn — in-memory speed caches (butter-smooth under 1000s of users)

import asyncio
import time


class TTLCache:
    """Tiny async TTL cache with size cap + cleanup."""

    __slots__ = ("ttl", "maxsize", "_data", "_lock")

    def __init__(self, ttl: float = 60.0, maxsize: int = 20000):
        self.ttl = ttl
        self.maxsize = maxsize
        self._data = {}
        self._lock = asyncio.Lock()

    async def get(self, key, default=None):
        if not self._data:
            return default
        item = self._data.get(key)
        if item is None:
            return default
        value, expire = item
        if expire < time.monotonic():
            self._data.pop(key, None)
            return default
        return value

    def get_sync(self, key, default=None):
        item = self._data.get(key)
        if item is None:
            return default
        value, expire = item
        if expire < time.monotonic():
            self._data.pop(key, None)
            return default
        return value

    async def set(self, key, value):
        if len(self._data) >= self.maxsize:
            now = time.monotonic()
            for k in [k for k, (_, e) in self._data.items() if e < now]:
                self._data.pop(k, None)
        self._data[key] = (value, time.monotonic() + self.ttl)

    def set_sync(self, key, value):
        if len(self._data) >= self.maxsize:
            now = time.monotonic()
            for k in [k for k, (_, e) in self._data.items() if e < now]:
                self._data.pop(k, None)
        self._data[key] = (value, time.monotonic() + self.ttl)

    async def invalidate(self, key):
        self._data.pop(key, None)


# ── Global caches ─────────────────────────────────────────────────────────────

settings_cache = TTLCache(ttl=30.0, maxsize=20000)      # user_id -> settings doc
premium_cache = TTLCache(ttl=60.0, maxsize=20000)       # "_all_" -> set of premium ids
users_count_cache = TTLCache(ttl=300.0, maxsize=1)      # "_count_" -> int
file_id_cache = TTLCache(ttl=86400.0, maxsize=50000)    # source key -> dest file_id payload
thumb_cache = TTLCache(ttl=86400.0, maxsize=20000)      # source key -> thumb file_id/path


class Pacing:
    """Adaptive flood-safe pacer. 3s baseline, backs off only on FloodWait."""

    def __init__(self, base: float = 3.0, cap: float = 30.0):
        self.base = base
        self.cap = cap
        self.interval = base

    async def wait(self):
        await asyncio.sleep(self.interval)

    def on_floodwait(self, seconds: float):
        self.interval = min(self.cap, max(self.base, seconds + 1))

    def on_success(self):
        self.interval = max(self.base, self.interval * 0.9)
