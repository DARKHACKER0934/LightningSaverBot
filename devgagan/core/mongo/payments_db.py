# ---------------------------------------------------
# File Name: payments_db.py
# Description: MongoDB storage for pending UPI payments
# ---------------------------------------------------

import datetime
from motor.motor_asyncio import AsyncIOMotorClient as MongoCli
from config import MONGO_DB

mongo = MongoCli(MONGO_DB)
_db = mongo.payment_data
payments_col = _db.pending_payments

async def create_payment_record(order_id: str, user_id: int, plan_days: int, amount: int):
    await payments_col.update_one(
        {"order_id": order_id},
        {"$set": {
            "order_id": order_id,
            "user_id": user_id,
            "plan_days": plan_days,
            "amount": amount,
            "status": "PENDING",
            "created_at": datetime.datetime.utcnow(),
        }},
        upsert=True
    )

async def get_payment_record(order_id: str, user_id: int):
    return await payments_col.find_one({"order_id": order_id, "user_id": user_id})

async def get_pending_payment_by_user(user_id: int):
    return await payments_col.find_one(
        {"user_id": user_id, "status": "PENDING"},
        sort=[("created_at", -1)]
    )

async def mark_payment_done(order_id: str, user_id: int):
    await payments_col.update_one(
        {"order_id": order_id, "user_id": user_id},
        {"$set": {"status": "COMPLETED"}}
    )

async def mark_payment_failed(order_id: str, user_id: int):
    await payments_col.update_one(
        {"order_id": order_id, "user_id": user_id},
        {"$set": {"status": "FAILED"}}
    )
