# devggn — premium plans (cached premium set, no repeated scans)

import datetime

from motor.motor_asyncio import AsyncIOMotorClient as MongoCli
from config import MONGO_DB
from devgagan.core.cache import premium_cache

mongo = MongoCli(MONGO_DB)
db = mongo.premium
db = db.premium_db


async def add_premium(user_id, expire_date):
    data = await check_premium(user_id)
    if data and data.get("_id"):
        await db.update_one({"_id": user_id}, {"$set": {"expire_date": expire_date}})
    else:
        await db.insert_one({"_id": user_id, "expire_date": expire_date})
    await premium_cache.invalidate("_all_")


async def remove_premium(user_id):
    await db.delete_one({"_id": user_id})
    await premium_cache.invalidate("_all_")


async def check_premium(user_id):
    return await db.find_one({"_id": user_id})


async def premium_users():
    id_list = []
    async for data in db.find({"_id": {"$gt": 0}}):
        id_list.append(data["_id"])
    return id_list


async def check_and_remove_expired_users():
    current_time = datetime.datetime.utcnow()
    removed = False
    async for data in db.find():
        expire_date = data.get("expire_date")
        if expire_date and expire_date < current_time:
            await remove_premium(data["_id"])
            removed = True
            print(f"Removed user {data['_id']} due to expired plan.")
    if removed:
        await premium_cache.invalidate("_all_")
