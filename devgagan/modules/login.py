# devggn — login flow (3-step styled)

import random
import os
import string

from pyrogram import filters, Client
from devgagan import app
from pyromod import listen
from devgagan.core.mongo import db
from devgagan.core.func import subscribe
from config import API_ID as api_id, API_HASH as api_hash
from pyrogram.errors import (
    ApiIdInvalid,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
    FloodWait,
)


def generate_random_name(length=7):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))


async def delete_session_files(user_id):
    session_file = f"session_{user_id}.session"
    memory_file = f"session_{user_id}.session-journal"

    session_file_exists = os.path.exists(session_file)
    memory_file_exists = os.path.exists(memory_file)

    if session_file_exists:
        os.remove(session_file)

    if memory_file_exists:
        os.remove(memory_file)

    data = await db.get_data(user_id) or {}
    had_session = bool(data.get("session"))
    await db.delete_session(user_id)
    return session_file_exists or memory_file_exists or had_session


@app.on_message(filters.command("logout") & filters.private)
async def clear_db(client, message):
    user_id = message.chat.id
    files_deleted = await delete_session_files(user_id)

    if files_deleted:
        await message.reply("✅ **Logged out!** Your session data has been cleared from memory and disk.")
    else:
        await message.reply("⚠️ You are not logged in, no session data found.")


@app.on_message(filters.command("login") & filters.private)
async def generate_session(_, message):
    joined = await subscribe(_, message)
    if joined == 1:
        return

    user_id = message.chat.id

    try:
        number = await _.ask(
            user_id,
            "📱 **STEP 1/3**\n\nPlease enter your phone number along with the country code.\nExample: `+19876543210`",
            filters=filters.text,
            timeout=300,
        )
    except TimeoutError:
        await message.reply("⏰ Time limit exceeded. Please /login again.")
        return
    phone_number = number.text
    try:
        await message.reply("📲 Sending OTP...")
        client = Client(f"session_{user_id}", api_id, api_hash)
        await client.connect()
    except Exception:
        await message.reply("❌ Failed to send OTP. Please wait and try again later.")
        return
    try:
        code = await client.send_code(phone_number)
    except ApiIdInvalid:
        await message.reply('❌ Invalid combination of API ID and API HASH. Please restart the session.')
        return
    except PhoneNumberInvalid:
        await message.reply('❌ Invalid phone number. Please restart the session.')
        return
    except FloodWait as fw:
        await message.reply(f'⚠️ Too many attempts. Try again after `{fw.x}` seconds.')
        return
    try:
        otp_code = await _.ask(
            user_id,
            "🔐 **STEP 2/3**\n\nOTP has been sent to your Telegram account.\nEnter the OTP like this:\nIf OTP is `12345`, enter `1 2 3 4 5`",
            filters=filters.text,
            timeout=600,
        )
    except TimeoutError:
        await message.reply('⏰ Time limit of 10 minutes exceeded. Please /login again.')
        return
    phone_code = otp_code.text.replace(" ", "")
    try:
        await client.sign_in(phone_number, code.phone_code_hash, phone_code)
    except PhoneCodeInvalid:
        await message.reply('❌ Invalid OTP. Please /login again.')
        return
    except PhoneCodeExpired:
        await message.reply('❌ Expired OTP. Please /login again.')
        return
    except SessionPasswordNeeded:
        try:
            two_step_msg = await _.ask(
                user_id,
                '🔑 **STEP 3/3**\n\nYour account has two-step verification enabled.\nPlease enter your password.',
                filters=filters.text,
                timeout=300,
            )
        except TimeoutError:
            await message.reply('⏰ Time limit of 5 minutes exceeded. Please /login again.')
            return
        try:
            password = two_step_msg.text
            await client.check_password(password=password)
        except PasswordHashInvalid:
            await two_step_msg.reply('❌ Invalid password. Please /login again.')
            return
    string_session = await client.export_session_string()
    await db.set_session(user_id, string_session)
    await client.disconnect()
    await otp_code.reply(
        "🎉 **LOGIN SUCCESSFUL!**\n\n"
        "✅ Your session is now active.\n"
        "⚡ You can now save from private channels too!\n\n"
        "Send any post link to start saving 🚀"
    )
