# devgaganin — premium plan management (owner + user views)

import datetime
import pytz

from devgagan import app
from config import OWNER_ID
from devgagan.core.func import get_seconds
from devgagan.core.mongo import plans_db
from pyrogram import filters

IST = pytz.timezone("Asia/Kolkata")


@app.on_message(filters.command("rem") & filters.user(OWNER_ID))
async def remove_premium(client, message):
    if len(message.command) == 2:
        user_id = int(message.command[1])
        user = await client.get_users(user_id)
        data = await plans_db.check_premium(user_id)

        if data and data.get("_id"):
            await plans_db.remove_premium(user_id)
            await message.reply_text("✅ **Premium removed successfully!**")
            await client.send_message(
                chat_id=user_id,
                text=f"Hey {user.mention},\n\n❌ Your premium access has been removed.\nThanks for using our service 😊",
            )
        else:
            await message.reply_text("⚠️ Unable to remove — that user never had premium.")
    else:
        await message.reply_text("Usage : /rem user_id")


@app.on_message(filters.command("myplan") & filters.private)
async def myplan(client, message):
    user_id = message.from_user.id
    user = message.from_user.mention
    data = await plans_db.check_premium(user_id)
    if data and data.get("expire_date"):
        expiry = data.get("expire_date")
        expiry_ist = expiry.astimezone(IST)
        expiry_str = expiry_ist.strftime("%d-%m-%Y • %I:%M %p")

        current_time = datetime.datetime.now(IST)
        time_left = expiry_ist - current_time

        days = time_left.days
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, _ = divmod(remainder, 60)

        time_left_str = f"{days}d {hours}h {minutes}m"
        await message.reply_text(
            f"💎 **PREMIUM DASHBOARD**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User** : {user}\n"
            f"⚡ **User ID** : <code>{user_id}</code>\n"
            f"⏰ **Time Left** : `{time_left_str}`\n"
            f"⌛️ **Expiry Date** : `{expiry_str}`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 Enjoy unlimited speed & batches!"
        )
    else:
        await message.reply_text(
            f"Hey {user},\n\n"
            f"⚠️ You don't have any active premium plan.\n"
            f"💎 Use /pay to unlock all premium features!"
        )


@app.on_message(filters.command("check") & filters.user(OWNER_ID))
async def get_premium(client, message):
    if len(message.command) == 2:
        user_id = int(message.command[1])
        user = await client.get_users(user_id)
        data = await plans_db.check_premium(user_id)
        if data and data.get("expire_date"):
            expiry = data.get("expire_date")
            expiry_ist = expiry.astimezone(IST)
            expiry_str = expiry_ist.strftime("%d-%m-%Y • %I:%M %p")

            current_time = datetime.datetime.now(IST)
            time_left = expiry_ist - current_time

            days = time_left.days
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)

            time_left_str = f"{days}d {hours}h {minutes}m"
            await message.reply_text(
                f"💎 **PREMIUM CHECK**\n\n"
                f"👤 **User** : {user.mention}\n"
                f"⚡ **User ID** : <code>{user_id}</code>\n"
                f"⏰ **Time Left** : `{time_left_str}`\n"
                f"⌛️ **Expiry** : `{expiry_str}`"
            )
        else:
            await message.reply_text("❌ No premium data found for this user.")
    else:
        await message.reply_text("Usage : /check user_id")


@app.on_message(filters.command("add") & filters.user(OWNER_ID))
async def give_premium_cmd_handler(client, message):
    if len(message.command) == 4:
        time_zone = datetime.datetime.now(IST)
        current_time = time_zone.strftime("%d-%m-%Y • %I:%M %p")
        user_id = int(message.command[1])
        user = await client.get_users(user_id)
        time = message.command[2] + " " + message.command[3]
        seconds = await get_seconds(time)
        if seconds > 0:
            expiry_time = datetime.datetime.now(pytz.utc) + datetime.timedelta(seconds=seconds)
            await plans_db.add_premium(user_id, expiry_time)
            data = await plans_db.check_premium(user_id)
            expiry = data.get("expire_date")
            expiry_str = expiry.astimezone(IST).strftime("%d-%m-%Y • %I:%M %p")
            await message.reply_text(
                f"✅ **PREMIUM ADDED SUCCESSFULLY**\n\n"
                f"👤 **User** : {user.mention}\n"
                f"⚡ **User ID** : <code>{user_id}</code>\n"
                f"⏰ **Plan** : <code>{time}</code>\n"
                f"⌛️ **Expiry** : `{expiry_str}`",
                disable_web_page_preview=True,
            )
            await client.send_message(
                chat_id=user_id,
                text=(
                    f"🎉 Hey {user.mention},\n"
                    f"Thank you for purchasing premium!\n\n"
                    f"💎 **Plan** : <code>{time}</code>\n"
                    f"⌛️ **Expiry** : `{expiry_str}`\n\n"
                    f"Enjoy lightning-fast saving ⚡✨"
                ),
                disable_web_page_preview=True,
            )
        else:
            await message.reply_text(
                "Invalid time format. Use '1 day', '1 hour', '1 min', '1 month' or '1 year'."
            )
    else:
        await message.reply_text("Usage : /add user_id time (e.g., '1 day' or '1 month')")
