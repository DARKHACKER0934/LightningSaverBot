from pyrogram import filters
from devgagan import app
from devgagan.core import script
from devgagan.core.func import subscribe
from config import OWNER_ID
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# ------------------- Start-Buttons ------------------- #

buttons = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("Join Channel", url="https://t.me/rajputserver")],
        [InlineKeyboardButton("💎 Buy Premium", callback_data="get_premium_btn")],
    ]
)

@app.on_message(filters.command("start"))
async def start(_, message):
    join = await subscribe(_, message)
    if join == 1:
        return

    # ❌ "https://t.me/rajputserver/19" photo ke liye valid nahi hai
    # ✅ Ya to direct image link do (jpg/png), ya ek baar image bhejkar uska file_id use karo

    await message.reply_photo(
        photo="https://i.postimg.cc/vH7NtYnn/file-00000000df88622fab8ba266a852d070.png",  # yahan apna direct image link ya file_id dalna
        caption=script.START_TXT.format(message.from_user.mention),
        reply_markup=buttons
    )
