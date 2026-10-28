# devggn — boot: single client start, userbot pool janitor, memory + expiry jobs

import asyncio
import gc
import importlib

import psutil
from pyrogram import idle

from devgagan import app, BOT_ID, BOT_NAME, BOT_USERNAME
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
    await app.start()
    getme = await app.get_me()
    BOT_ID = getme.id
    BOT_USERNAME = getme.username
    BOT_NAME = (getme.first_name + " " + (getme.last_name or "")).strip()

    for all_module in ALL_MODULES:
        importlib.import_module("devgagan.modules." + all_module)

    print(f"⚡ Bot started successfully as @{BOT_USERNAME} — lightning engine online!")

    asyncio.get_running_loop().create_task(schedule_expiry_check())
    asyncio.get_running_loop().create_task(optimize_memory())
    userbot_pool.start_janitor(asyncio.get_running_loop())

    await idle()
    await app.stop()


if __name__ == "__main__":
    asyncio.run(devggn_boot())
