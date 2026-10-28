# devggn — clean, single-start bot core

import logging
import os

from pyrogram import Client
from config import API_ID, API_HASH, BOT_TOKEN

logging.basicConfig(
    format="[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s",
    level=logging.INFO,
)

# Download cache dir (reused media skip re-downloads, cached thumbnails live here)
os.makedirs("/app/downloads", exist_ok=True)
os.makedirs("/app/downloads/thumbs", exist_ok=True)

app = Client(
    ":LightningBot:",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=32,
    sleep_threshold=30,
    max_concurrent_transmissions=10,
)

BOT_ID = None
BOT_NAME = ""
BOT_USERNAME = ""
