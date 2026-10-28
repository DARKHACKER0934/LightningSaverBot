#devggn

from config import MONGO_DB
from motor.motor_asyncio import AsyncIOMotorClient as MongoCli

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


async def set_thumbnail(user_id, thumb):
    await db.update_one({"_id": user_id}, {"$set": {"thumb": thumb}}, upsert=True)


async def set_caption(user_id, caption):
    await db.update_one({"_id": user_id}, {"$set": {"caption": caption}}, upsert=True)

async def set_rename_tag(user_id, rename_tag):
    await db.update_one({"_id": user_id}, {"$set": {"rename_tag": rename_tag}}, upsert=True)

async def remove_rename_tag(user_id):
    await db.update_one({"_id": user_id}, {"$unset": {"rename_tag": ""}})

async def set_replace_words(user_id, rules):
    await db.update_one({"_id": user_id}, {"$set": {"replacement_words": rules}}, upsert=True)

async def set_clean_words(user_id, words):
    await db.update_one({"_id": user_id}, {"$set": {"clean_words": words}}, upsert=True)

async def reset_all_settings(user_id):
    await db.update_one(
        {"_id": user_id},
        {"$unset": {
            "thumb": "", "caption": "", "rename_tag": "", "chat_id": "",
            "replace_txt": "", "to_replace": "", "replacement_words": "", "clean_words": ""
        }}
    )

async def remove_all_local_word_rules(user_id):
    await db.update_one(
        {"_id": user_id},
        {"$unset": {"delete_words": "", "replacement_words": ""}}
    )


async def replace_caption(user_id, replace_txt, to_replace):
    await db.update_one(
        {"_id": user_id},
        {"$set": {"replace_txt": replace_txt, "to_replace": to_replace}},
        upsert=True,
    )


async def set_session(user_id, session):
    await db.update_one({"_id": user_id}, {"$set": {"session": session}}, upsert=True)



async def clean_words(user_id, new_clean_words):
    data = await get_data(user_id)
    if data and data.get("_id"):
        existing_words = data.get("clean_words", [])
        # Ensure existing_words is a list
        if existing_words is None:
            existing_words = []
        updated_words = list(set(existing_words + new_clean_words))
        await db.update_one({"_id": user_id}, {"$set": {"clean_words": updated_words}})
    else:
        await db.insert_one({"_id": user_id, "clean_words": new_clean_words})


async def remove_clean_words(user_id, words_to_remove):
    data = await get_data(user_id)
    if data and data.get("_id"):
        existing_words = data.get("clean_words", [])
        updated_words = [word for word in existing_words if word not in words_to_remove]
        await db.update_one({"_id": user_id}, {"$set": {"clean_words": updated_words}})
    else:
        await db.insert_one({"_id": user_id, "clean_words": []})


async def set_channel(user_id, chat_id):
    await db.update_one({"_id": user_id}, {"$set": {"chat_id": chat_id}}, upsert=True)



async def all_words_remove(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"clean_words": None}})

async def remove_thumbnail(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"thumb": None}})

async def remove_caption(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"caption": None}})

async def remove_replace(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"replace_txt": None, "to_replace": None}})
    
async def remove_session(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"session": None}})

async def remove_channel(user_id):
    await db.update_one({"_id": user_id}, {"$set": {"chat_id": None}})

async def delete_session(user_id):
    """Delete the session associated with the given user_id from the database."""
    await db.update_one({"_id": user_id}, {"$unset": {"session": ""}})
