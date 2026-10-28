# devggn — /help center with button navigation

from pyrogram import filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from devgagan import app
from devgagan.core import script
from devgagan.core.func import subscribe


HELP_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📖 Commands", callback_data="hlp_commands"),
     InlineKeyboardButton("🕵️ Saving Guide", callback_data="hlp_guide")],
    [InlineKeyboardButton("❓ FAQ", callback_data="hlp_faq"),
     InlineKeyboardButton("💎 Premium", callback_data="get_premium_btn")],
])


@app.on_message(filters.command("help") & filters.private)
async def help_cmd(_, message):
    if await subscribe(_, message) == 1:
        return
    await message.reply(
        script.HELP_TXT.format(line=script.LINE),
        reply_markup=HELP_MENU,
    )


@app.on_callback_query(filters.regex(r"^hlp_(commands|guide|faq)$"))
async def help_nav(_, cb: CallbackQuery):
    action = cb.matches[0].group(1)
    await cb.answer()
    if action == "commands":
        await cb.message.edit_text(
            script.HELP_TXT.format(line=script.LINE),
            reply_markup=HELP_MENU,
        )
    elif action == "guide":
        await cb.message.edit_text(
            script.HELP2_TXT.format(line=script.LINE),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="hlp_commands")],
            ]),
        )
    elif action == "faq":
        await cb.message.edit_text(
            "❓ **FREQUENTLY ASKED QUESTIONS**\n\n"
            f"{script.LINE}\n\n"
            "**1. Bot says \"Have you joined the channel?\"**\n"
            "→ Simply /login again in the bot and retry.\n\n"
            "**2. Batch is stuck?**\n"
            "→ Use /cancel to stop it and start fresh.\n\n"
            "**3. Login expired?**\n"
            "→ Sessions expire sometimes. Just /login again.\n\n"
            "**4. Want faster saving?**\n"
            "→ Upgrade to premium 💎 for bigger batches and priority speed.\n\n"
            f"{script.LINE}\n"
            "Still stuck? Contact support! 🙏",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="hlp_commands")],
            ]),
        )
