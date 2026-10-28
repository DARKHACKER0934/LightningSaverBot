# devggn — boot: single client start, userbot pool janitor, memory + expiry jobs

import asyncio
import gc
import importlib

import psutil
from pyrogram import idle

from devgagan import create_app, loop
from devgagan.modules import ALL_MODULES
from devgagan.core.mongo.plans_db import check_and_remove_expired_users
from devgagan.core.pool import userbot_pool

MEMORY_OPTIMIZATION_INTERVAL = 600  # 10 minutes


async def optimize_memory():
    while True:
        gc.collect()
        process = psutil.Process()
        memory_info = process.memory_info()
        print(f"Memory Usage: {memory_info.rss / 1024 / 1024:.2f} MB")
        await asyncio.sleep(MEMORY_OPTIMIZATION_INTERVAL)


async def schedule_expiry_check():
    while True:
        try:
            await check_and_remove_expired_users()
        except Exception as e:
            print(f"Expiry check failed: {e}")
        await asyncio.sleep(3600)  # Check every hour


async def devggn_boot():
    # Construct the client INSIDE the running loop (Pyrogram binds self.loop
    # at construction time — creating it at import caused cross-loop crashes).
    app = create_app()
    await app.start()
    getme = await app.get_me()
    bot_username = getme.username
    bot_name = (getme.first_name + " " + (getme.last_name or "")).strip()

    for all_module in ALL_MODULES:
        importlib.import_module("devgagan.modules." + all_module)

    print(f"⚡ Bot started successfully as @{bot_username} — lightning engine online!")

    asyncio.get_running_loop().create_task(schedule_expiry_check())
    asyncio.get_running_loop().create_task(optimize_memory())
    userbot_pool.start_janitor(asyncio.get_running_loop())

    await idle()
    await app.stop()


if __name__ == "__main__":
    # Must NOT use asyncio.run() here: pyromod's sync wrappers capture the
    # import-time loop and schedule handlers back onto it (see devgagan/__init__).
    try:
        loop.run_until_complete(devggn_boot())
    except KeyboardInterrupt:
        pass
    finally:
        try:
            loop.close()
        except Exception:
            pass
