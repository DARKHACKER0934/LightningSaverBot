# devggn — users storage (O(1) existence checks, no full scans)

from motor.motor_asyncio import AsyncIOMotorClient as MongoCli
from config import MONGO_DB
from devgagan.core.cache import users_count_cache

mongo = MongoCli(MONGO_DB)
db = mongo.users
db = db.users_db


async def get_user(user):
    """O(1) existence check via indexed lookup."""
    doc = await db.users.find_one({"user": user}, {"_id": 1})
    return doc is not None


async def add_user(user):
    if not await get_user(user):
        await db.users.insert_one({"user": user})
        await users_count_cache.invalidate("_count_")


async def get_users():
    user_list = []
    async for user in db.users.find({"user": {"$gt": 0}}):
        user_list.append(user['user'])
    return user_list


async def count_users():
    cached = await users_count_cache.get("_count_")
    if cached is not None:
        return cached
    count = await db.users.count_documents({"user": {"$gt": 0}})
    await users_count_cache.set("_count_", count)
    return count


async def del_user(user):
    await db.users.delete_one({"user": user})
    await users_count_cache.invalidate("_count_")
