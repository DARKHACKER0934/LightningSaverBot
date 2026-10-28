"""Runtime configuration loaded from Hugging Face Space secrets/environment variables."""

import os


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Add it under Hugging Face Space Settings > Variables and secrets."
        )
    return value


API_ID = int(required("API_ID"))
API_HASH = required("API_HASH")
BOT_TOKEN = required("BOT_TOKEN")
OWNER_ID = [int(value) for value in required("OWNER_ID").split()]
MONGO_DB = required("MONGO_DB")
LOG_GROUP = required("LOG_GROUP")
CHANNEL_ID = int(required("CHANNEL_ID"))
FREEMIUM_LIMIT = int(os.getenv("FREEMIUM_LIMIT", "0"))
PREMIUM_LIMIT = int(os.getenv("PREMIUM_LIMIT", "5000"))
ZAPUPI_TOKEN = os.getenv("ZAPUPI_TOKEN", "")
