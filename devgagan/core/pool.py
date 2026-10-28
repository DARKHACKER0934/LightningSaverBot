# devggn — userbot session pool (instant job start, no per-link client boot)

import asyncio
import random
import string
import time

from pyrogram import Client
from config import API_ID, API_HASH

IDLE_TIMEOUT = 600          # keep userbot warm for 10 min after last use
MAX_WARM_BOTS = 25          # cap memory
START_TIMEOUT = 30


def _random_name():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


class UserbotPool:
    def __init__(self):
        self._slots = {}          # user_id -> {"client":..., "session":..., "last_used":..., "busy":False}
        self._evict_task = None
        self._cleanup_task = None

    def start_janitor(self, loop):
        if self._evict_task is None:
            self._evict_task = loop.create_task(self._janitor())

    async def _janitor(self):
        while True:
            await asyncio.sleep(120)
            now = time.monotonic()
            for uid, slot in list(self._slots.items()):
                if not slot["busy"] and now - slot["last_used"] > IDLE_TIMEOUT:
                    try:
                        await slot["client"].stop()
                    except Exception:
                        pass
                    self._slots.pop(uid, None)
            if len(self._slots) > MAX_WARM_BOTS:
                idle = sorted(
                    (s for s in self._slots.values() if not s["busy"]),
                    key=lambda s: s["last_used"],
                )
                for slot in idle[: max(1, len(idle) - MAX_WARM_BOTS)]:
                    try:
                        await slot["client"].stop()
                    except Exception:
                        pass
                    self._slots.pop(slot.get("user_id"), None)

    async def acquire(self, user_id: int, session_string: str):
        """Return a connected, reusable userbot for user_id. Never None."""
        slot = self._slots.get(user_id)
        if slot and slot["busy"]:
            raise RuntimeError("This account is already processing another task.")
        if slot and slot["client"].is_connected:
            slot["busy"] = True
            slot["last_used"] = time.monotonic()
            return slot["client"]

        client = Client(
            _random_name(),
            api_id=API_ID,
            api_hash=API_HASH,
            device_model="pyrogram",
            session_string=session_string,
        )
        try:
            await asyncio.wait_for(client.start(), timeout=START_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                await client.stop()
            except Exception:
                pass
            raise TimeoutError("Userbot startup timed out.")
        except Exception:
            try:
                await client.stop()
            except Exception:
                pass
            raise
        self._slots[user_id] = {
            "user_id": user_id,
            "client": client,
            "session": session_string,
            "last_used": time.monotonic(),
            "busy": True,
        }
        return client

    def release(self, user_id: int):
        slot = self._slots.get(user_id)
        if slot:
            slot["busy"] = False
            slot["last_used"] = time.monotonic()

    async def drop(self, user_id: int):
        slot = self._slots.pop(user_id, None)
        if slot:
            try:
                await slot["client"].stop()
            except Exception:
                pass

    def is_busy(self, user_id: int) -> bool:
        slot = self._slots.get(user_id)
        return bool(slot and slot["busy"])


userbot_pool = UserbotPool()
