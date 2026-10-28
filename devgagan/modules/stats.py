# devggn — stats with O(1) user tracking (no full-collection scans per message)

from devgagan import app
from pyrogram import filters
from config import OWNER_ID
from devgagan.core.mongo.users_db import get_user, add_user, count_users
from devgagan.core.func import premium_users
import asyncio

_user_seen = set()
_lock = asyncio.Lock()


@app.on_message(group=10)
async def chat_watcher_func(_, message):
    try:
        if message.from_user:
            uid = message.from_user.id
            if uid in _user_seen:
                return
            async with _lock:
                if uid in _user_seen:
                    return
                try:
                    if not await get_user(uid):
                        await add_user(uid)
                    _user_seen.add(uid)
                    if len(_user_seen) > 100000:
                        _user_seen.clear()
                except Exception:
                    pass
    except Exception:
        pass


@app.on_message(filters.command("stats") & filters.user(OWNER_ID))
async def stats(client, message):
    users = await count_users()
    premium = await premium_users()
    me = await client.get_me()
    await message.reply_text(
        f"📊 **BOT DASHBOARD**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🤖 **Bot** : {me.mention}\n"
        f"👥 **Total Users** : `{users}`\n"
        f"💎 **Premium Users** : `{len(premium)}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ **Status** : Running at lightning speed"
    )
