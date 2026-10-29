from pyrogram import filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from devgagan import app
from devgagan.core import script
from devgagan.core.func import subscribe
from config import OWNER_ID, CHANNEL_ID


# ── "I've Joined" verification button ─────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^verifyjoin_(\d+)$"))
async def verify_join(_, cb: CallbackQuery):
    uid = int(cb.matches[0].group(1))
    if cb.from_user.id != uid:
        return await cb.answer("This button is not for you.", show_alert=True)
    try:
        member = await app.get_chat_member(CHANNEL_ID, uid)
        if member.status in ("kicked", "banned", "restricted"):
            await cb.answer("🚫 You are banned from the channel.", show_alert=True)
            return
        await cb.answer("✅ Verified! Ab /start ya link dobara bhejo.", show_alert=True)
        try:
            await cb.message.delete()
        except Exception:
            pass
    except Exception:
        await cb.answer("❌ Abhi tak joined nahi ho. Pehle channel join karo, phir click karo.", show_alert=True)

# ── Main menu buttons ─────────────────────────────────────────────────────────

def main_menu(user_id):
    rows = [
        [
            InlineKeyboardButton("📦 Batch", callback_data=f"mn_batch_{user_id}"),
            InlineKeyboardButton("⚙️ Settings", callback_data=f"mn_settings_{user_id}"),
        ],
        [
            InlineKeyboardButton("🔐 Login", callback_data=f"mn_login_{user_id}"),
            InlineKeyboardButton("💎 Premium", callback_data="get_premium_btn"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data=f"mn_help_{user_id}"),
            InlineKeyboardButton("📊 My Plan", callback_data=f"mn_plan_{user_id}"),
        ],
    ]
    if user_id in OWNER_ID:
        rows.append([InlineKeyboardButton("👑 Owner Panel", callback_data=f"mn_admin_{user_id}")])
    return InlineKeyboardMarkup(rows)


@app.on_message(filters.command("start"))
async def start(_, message):
    join = await subscribe(_, message)
    if join == 1:
        return

    uid = message.from_user.id
    await message.reply_photo(
        photo="https://i.postimg.cc/vH7NtYnn/file-00000000df88622fab8ba266a852d070.png",
        caption=script.START_TXT.format(message.from_user.mention, line=script.LINE),
        reply_markup=main_menu(uid),
    )


# ── Main menu navigation ──────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^mn_(batch|settings|login|help|plan|admin)_(\d+)$"))
async def menu_nav(_, cb: CallbackQuery):
    uid = int(cb.matches[0].group(2))
    if cb.from_user.id != uid:
        return await cb.answer("This menu is not for you.", show_alert=True)
    action = cb.matches[0].group(1)
    await cb.answer()

    if action == "batch":
        await cb.message.reply(
            "📦 **BATCH MODE**\n\n"
            "Send /batch and I'll ask you for the start and end links. "
            "I'll save everything in between at turbo speed ⚡"
        )
    elif action == "settings":
        from devgagan.modules.settings import settings_keyboard, SETTINGS_TEXT
        await cb.message.reply(SETTINGS_TEXT, reply_markup=settings_keyboard(uid))
    elif action == "login":
        await cb.message.reply(
            "🔐 **LOGIN**\n\n"
            "Send /login and follow the steps:\n"
            "1️⃣ Phone number\n"
            "2️⃣ OTP code\n"
            "3️⃣ 2FA password (if any)\n\n"
            "Once logged in, you can save from private channels too! 🎉"
        )
    elif action == "plan":
        await cb.message.reply(
            "📊 Use /myplan to check your current plan status, "
            "or /pay to buy premium 💎"
        )
    elif action == "help":
        from devgagan.modules.help import HELP_MENU
        await cb.message.reply(
            script.HELP_TXT.format(line=script.LINE),
            reply_markup=HELP_MENU,
        )
    elif action == "admin":
        await cb.message.reply(script.ADMIN_TXT.format(line=script.LINE))
