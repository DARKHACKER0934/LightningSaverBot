# devggn — clean, single-start bot core

import asyncio
import logging
import os

# pyromod MUST be imported before any Client is constructed — it patches
# pyrogram.client.Client at import time (adds the `listeners` machinery).
from pyromod import listen  # noqa: F401
from pyrogram import Client
from config import API_ID, API_HASH, BOT_TOKEN

logging.basicConfig(
    format="[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s",
    level=logging.INFO,
)

# Download cache dir (reused media skip re-downloads, cached thumbnails live here)
os.makedirs("/app/downloads", exist_ok=True)
os.makedirs("/app/downloads/thumbs", exist_ok=True)

# pyromod's sync wrappers capture this import-time loop and schedule handler
# coroutines back onto it. The bot MUST run on this same loop (asyncio.run()
# creates a fresh loop and breaks pyromod — verified crash on Python 3.12).
loop = asyncio.get_event_loop()

# The Client is constructed lazily, INSIDE this loop (Pyrogram binds
# self.loop at construction time).
app = None

BOT_ID = None
BOT_NAME = ""
BOT_USERNAME = ""


def create_app():
    global app
    app = Client(
        ":LightningBot:",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workers=32,
        sleep_threshold=30,
        max_concurrent_transmissions=10,
    )
    return app
