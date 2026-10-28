# devggn — unified async MongoDB layer (single connection pool + TTL cache)

import asyncio

from motor.motor_asyncio import AsyncIOMotorClient as MongoCli
from config import MONGO_DB

mongo = MongoCli(
    MONGO_DB,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=10000,
)
db = mongo.user_data
db = db.users_data_db


async def get_data(user_id):
    x = await db.find_one({"_id": user_id})
    return x


async def cached_get_data(user_id):
    """30s TTL cached settings doc — removes most Mongo roundtrips."""
    from devgagan.core.cache import settings_cache

    data = await settings_cache.get(user_id)
    if data is not None:
        return data
    try:
        data = await asyncio.wait_for(db.find_one({"_id": user_id}), timeout=10)
    except asyncio.TimeoutError:
        raise
    except Exception:
        data = None
    data = data or {}
    await settings_cache.set(user_id, data)
    return data


async def invalidate_settings(user_id):
    from devgagan.core.cache import settings_cache

    await settings_cache.invalidate(user_id)


async def set_thumbnail(user_id, thumb):
    await db.update_one({"_id": user_id}, {"$set": {"thumb": thumb}}, upsert=True)
    await invalidate_settings(user_id)


async def set_caption(user_id, caption):
    await db.update_one({"_id": user_id}, {"$set": {"caption": caption}}, upsert=True)
    await invalidate_settings(user_id)


async def set_rename_tag(user_id, rename_tag):
    await db.update_one({"_id": user_id}, {"$set": {"rename_tag": rename_tag}}, upsert=True)
    await invalidate_settings(user_id)


async def remove_rename_tag(user_id):
    await db.update_one({"_id": user_id}, {"$unset": {"rename_tag": ""}})
    await invalidate_settings(user_id)


async def set_replace_words(user_id, rules):
    await db.update_one({"_id": user_id}, {"$set": {"replacement_words": rules}}, upsert=True)
    await invalidate_settings(user_id)


async def set_clean_words(user_id, words):
    await db.update_one({"_id": user_id}, {"$set": {"clean_words": words}}, upsert=True)
    await invalidate_settings(user_id)


async def reset_all_settings(user_id):
    await db.update_one(
        {"_id": user_id},
        {"$unset": {
            "thumb": "", "caption": "", "rename_tag": "", "chat_id": "",
            "replace_txt": "", "to_replace": "", "replacement_words": "", "clean_words": ""
        }}
    )
    await invalidate_settings(user_id)


async def remove_all_local_word_rules(user_id):
    await db.update_one(
        {"_id": user_id},
        {"$unset": {"delete_words": "", "replacement_words": ""}}
    )
    await invalidate_settings(user_id)


async def replace_caption(user_id, replace_txt, to_replace):
    await db.update_one(
        {"_id": user_id},
        {"$set": {"replace_txt": replace_txt, "to_replace": to_replace}},
        upsert=True,
    )
    await invalidate_settings(user_id)


async def set_session(user_id, session):
    await db.update_one({"_id": user_id}, {"$set": {"session": session}}, upsert=True)


async def clean_words(user_id, new_clean_words):
    data = await get_data(user_id)
    if data and data.get("_id"):
        existing_words = data.get("clean_words", [])
        if existing_words is None:
            existing_words = []
        updated_words = list(set(existing_words + new_clean_words))
        await db.update_one({"_id": user_id}, {"$set": {"clean_words": updated_words}})
    else:
        await db.insert_one({"_id": user_id, "clean_words": new_clean_words})
    await invalidate_settings(user_id)


async def remove_clean_words(user_id, words_to_remove):
    data = await get_data(user_id)
    if data and data.get("_id"):
        existing_words = data.get("clean_words", [])
        updated_words = [word for word in existing_words if word not in words_to_remove]
        await db.update_one({"_id": user_id}, {"$set": {"clean_words": updated_words}})
    else:
        await db.insert_one({"_id": user_id, "clean_words": []})
    await invalidate_settings(user_id)


async def set_channel(user_id, chat_id):
    await db.update_one({"_id": user_id}, {"$set": {"chat_id": chat_id}}, upsert=True)
    await invalidate_settings(user_id)


async def all_words_remove(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"clean_words": None}})
    await invalidate_settings(user_id)


async def remove_thumbnail(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"thumb": None}})
    await invalidate_settings(user_id)


async def remove_caption(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"caption": None}})
    await invalidate_settings(user_id)


async def remove_replace(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"replace_txt": None, "to_replace": None}})
    await invalidate_settings(user_id)


async def remove_session(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"session": None}})


async def remove_channel(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"chat_id": None}})
    await invalidate_settings(user_id)


async def delete_session(user_id):
    """Delete the session associated with the given user_id from the database."""
    await db.update_one({"_id": user_id}, {"$unset": {"session": ""}})
