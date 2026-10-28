# devggn — flood-safe broadcast

import asyncio
import traceback

from pyrogram import filters
from pyrogram.errors import (
    FloodWait,
    InputUserDeactivated,
    PeerIdInvalid,
    UserIsBlocked,
)
from config import OWNER_ID
from devgagan import app
from devgagan.core.mongo.users_db import get_users


async def send_msg(user_id, message):
    try:
        await message.copy(chat_id=user_id)
        return "ok"
    except FloodWait as e:
        await asyncio.sleep(min(e.x, 60))
        return await send_msg(user_id, message)
    except InputUserDeactivated:
        return "deactivated"
    except UserIsBlocked:
        return "blocked"
    except PeerIdInvalid:
        return "invalid"
    except Exception:
        return "error"


async def _broadcast(message, exmsg, forward: bool = False):
    all_users = (await get_users()) or []
    done_users = 0
    failed_users = 0
    to_send = message.reply_to_message

    for i in range(0, len(all_users), 30):
        batch = all_users[i:i + 30]
        tasks = []
        for user in batch:
            tasks.append(send_msg(user, to_send))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if res == "ok":
                done_users += 1
            else:
                failed_users += 1
        if i + 30 < len(all_users):
            await asyncio.sleep(1)

    summary = (
        f"📣 **Broadcast Complete** ✅\n\n"
        f"✅ Sent to: `{done_users}` users\n"
        f"❌ Failed: `{failed_users}` users"
    )
    try:
        await exmsg.edit_text(summary)
    except Exception:
        pass


@app.on_message(filters.command("gcast") & filters.user(OWNER_ID))
async def broadcast(_, message):
    if not message.reply_to_message:
        await message.reply_text("❌ Reply to a message to broadcast it.")
        return
    exmsg = await message.reply_text("📡 Starting broadcast...")
    await _broadcast(message, exmsg)


@app.on_message(filters.command("announce") & filters.user(OWNER_ID))
async def announced(_, message):
    if not message.reply_to_message:
        return await message.reply_text("❌ Reply To Some Post To Broadcast")
    exmsg = await message.reply_text("📡 Starting announcement...")
    await _broadcast(message, exmsg, forward=True)
