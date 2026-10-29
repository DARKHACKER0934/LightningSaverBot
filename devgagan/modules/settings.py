import os
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from devgagan import app
from devgagan.core.mongo import db
from devgagan.core.func import subscribe
from devgagan.core.get_func import (
    set_rename_command, set_caption_command, user_chat_ids, user_caption_preferences,
    save_replacement_words, save_delete_words, load_delete_words,
)

_user_state = {}

SETTINGS_TEXT = (
    "⚙️ **SETTINGS PANEL** ⚙️\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Customize how your saved files look. Pick an option below 👇"
)


def settings_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Set Thumbnail", callback_data=f"stg_thumb_{user_id}"),
         InlineKeyboardButton("🗑 Remove Thumbnail", callback_data=f"stg_rthumb_{user_id}")],
        [InlineKeyboardButton("📝 Set Caption", callback_data=f"stg_caption_{user_id}"),
         InlineKeyboardButton("🗑 Remove Caption", callback_data=f"stg_rcaption_{user_id}")],
        [InlineKeyboardButton("✏️ Set Rename Tag", callback_data=f"stg_rename_{user_id}"),
         InlineKeyboardButton("🗑 Remove Rename", callback_data=f"stg_rrename_{user_id}")],
        [InlineKeyboardButton("📢 Set Chat ID", callback_data=f"stg_chatid_{user_id}"),
         InlineKeyboardButton("🗑 Remove Chat ID", callback_data=f"stg_rchatid_{user_id}")],
        [InlineKeyboardButton("🔤 Set Replace Words", callback_data=f"stg_replace_{user_id}"),
         InlineKeyboardButton("🗑 Remove Replace", callback_data=f"stg_rreplace_{user_id}")],
        [InlineKeyboardButton("🧹 Set Remove Words", callback_data=f"stg_cleanwords_{user_id}"),
         InlineKeyboardButton("🗑 Clear Remove Words", callback_data=f"stg_rclean_{user_id}")],
        [InlineKeyboardButton("🔄 Reset All", callback_data=f"stg_reset_{user_id}"),
         InlineKeyboardButton("📊 View Settings", callback_data=f"stg_view_{user_id}")],
    ])


def _owned(cb: CallbackQuery, uid: int) -> bool:
    return cb.from_user.id == uid


def _merge_remove_words(existing, additions):
    """Append new remove words while preserving existing entries and order."""
    merged = [str(word).strip() for word in (existing or []) if str(word).strip()]
    seen = {word.casefold() for word in merged}
    for word in additions:
        word = str(word).strip()
        if word and word.casefold() not in seen:
            merged.append(word)
            seen.add(word.casefold())
    return merged


@app.on_message(filters.command("settings") & filters.private)
async def settings_cmd(client, message: Message):
    if not message.from_user:
        return
    if await subscribe(client, message) == 1:
        return
    uid = message.from_user.id
    _user_state.pop(uid, None)
    await message.reply(SETTINGS_TEXT, reply_markup=settings_keyboard(uid))


@app.on_message(filters.command("cancel") & filters.private)
async def settings_cancel(_, message: Message):
    if message.from_user and _user_state.pop(message.from_user.id, None):
        await message.reply("✅ Settings input cancelled. Use /settings to open the panel again.")


@app.on_message(filters.private & filters.text & ~filters.command([
    "start", "help", "settings", "batch", "login", "logout", "cancel",
    "stats", "myplan", "add", "rem", "check", "gcast", "pay", "terms",
]))
async def settings_input(_, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    state = _user_state.get(uid)
    if not state:
        return
    text = (message.text or "").strip()
    action = state["action"]
    if not text and action in {"set_caption", "set_rename", "set_replace"}:
        return await message.reply("⚠️ Please send non-empty text, or use /cancel.")
    if action == "set_chatid":
        try:
            chat_id = int(text)
        except ValueError:
            return await message.reply("⚠️ Please send a numeric Chat ID, for example `-1001234567890`, or /cancel.")
        await db.set_channel(uid, chat_id)
        user_chat_ids[uid] = chat_id
    elif action == "set_caption":
        await db.set_caption(uid, text)
        await set_caption_command(uid, text)
    elif action == "set_rename":
        await db.set_rename_tag(uid, text)
        await set_rename_command(uid, text)
    elif action == "set_replace" and state.get("step", 1) == 1:
        state["old_word"] = text
        state["step"] = 2
        return await message.reply("🔁 Now send the replacement word. Send /cancel to abort.")
    elif action == "set_replace":
        old = state["old_word"]
        data = await db.get_data(uid) or {}
        rules = dict(data.get("replacement_words") or {})
        rules[old] = text
        await save_replacement_words(uid, rules)
        await db.set_replace_words(uid, rules)
    elif action == "set_cleanwords":
        additions = [w.strip() for w in text.split(",") if w.strip()]
        if not additions:
            return await message.reply("⚠️ Send one or more words separated by commas, for example `720p, x264`, or /cancel.")
        words = _merge_remove_words(await load_delete_words(uid), additions)
        await save_delete_words(uid, words)
        await db.set_clean_words(uid, words)
    else:
        return
    _user_state.pop(uid, None)
    await message.reply("✅ **Setting saved successfully!**")


@app.on_message(filters.private & filters.photo)
async def settings_photo_input(_, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    if _user_state.get(uid, {}).get("action") != "set_thumb":
        return
    await db.set_thumbnail(uid, message.photo.file_id)
    _user_state.pop(uid, None)
    await message.reply("✅ **Custom thumbnail saved successfully!**")


@app.on_callback_query(filters.regex(r"^stg_(thumb|rthumb|caption|rcaption|rename|rrename|chatid|rchatid|replace|rreplace|cleanwords|rclean|reset|view|back)_(\d+)$"))
async def settings_callback(_, cb: CallbackQuery):
    uid = int(cb.matches[0].group(2))
    if not _owned(cb, uid):
        return await cb.answer("This settings panel does not belong to you.", show_alert=True)
    action = cb.matches[0].group(1)
    if action == "view":
        d = await db.get_data(uid) or {}
        rules = d.get("replacement_words") or {}
        replacement = ", ".join(f"{k} → {v}" for k, v in rules.items()) or "Not set"
        words = ", ".join(d.get("clean_words") or []) or "Not set"
        text = ("📊 **CURRENT SETTINGS**\n\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🖼 Thumbnail: {'✅ Set' if d.get('thumb') else '❌ Not set'}\n"
                f"📝 Caption: {d.get('caption') or '❌ Not set'}\n"
                f"✏️ Rename tag: {d.get('rename_tag') or '❌ Not set'}\n"
                f"📢 Target chat: {d.get('chat_id') or '❌ Not set'}\n"
                f"🔤 Replace words: {replacement}\n"
                f"🧹 Remove words: {words}")
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"stg_back_{uid}")]]))
        return await cb.answer()
    if action == "back":
        await cb.message.edit_text(SETTINGS_TEXT, reply_markup=settings_keyboard(uid))
        return await cb.answer()
    if action == "reset":
        _user_state.pop(uid, None)
        await db.reset_all_settings(uid)
        if os.path.exists(f"{uid}.jpg"):
            os.remove(f"{uid}.jpg")
        user_chat_ids.pop(uid, None)
        user_caption_preferences.pop(str(uid), None)
        await set_caption_command(uid, "")
        await set_rename_command(uid, "")
        await save_delete_words(uid, [])
        await save_replacement_words(uid, {})
        await db.remove_all_local_word_rules(uid)
        await cb.message.edit_text(SETTINGS_TEXT, reply_markup=settings_keyboard(uid))
        return await cb.answer("Settings have been reset.")
    if action in {"rthumb", "rcaption", "rrename", "rchatid", "rreplace", "rclean"}:
        if action == "rthumb":
            await db.remove_thumbnail(uid)
            if os.path.exists(f"{uid}.jpg"):
                os.remove(f"{uid}.jpg")
        elif action == "rcaption":
            await db.remove_caption(uid)
            await set_caption_command(uid, "")
        elif action == "rrename":
            await db.remove_rename_tag(uid)
            await set_rename_command(uid, "")
        elif action == "rchatid":
            await db.remove_channel(uid)
            user_chat_ids.pop(uid, None)
        elif action == "rreplace":
            await db.remove_replace(uid)
            await db.set_replace_words(uid, {})
            await save_replacement_words(uid, {})
        elif action == "rclean":
            await db.all_words_remove(uid)
            await db.set_clean_words(uid, [])
            await save_delete_words(uid, [])
        return await cb.answer("✅ Removed.", show_alert=True)
    prompts = {
        "thumb": ("set_thumb", "🖼 Send the photo to use as the custom video/file thumbnail. Send /cancel to abort."),
        "caption": ("set_caption", "📝 Send the custom caption to add to processed files. Send /cancel to abort."),
        "rename": ("set_rename", "✏️ Send the tag to append to processed file names. Send /cancel to abort."),
        "chatid": ("set_chatid", "📢 Send the numeric ID of the destination channel or group. Send /cancel to abort."),
        "replace": ("set_replace", "🔤 Send the existing word or phrase to replace. Send /cancel to abort."),
        "cleanwords": ("set_cleanwords", "🧹 Send words to remove from captions and file names, separated by commas. Existing words will be kept. Send /cancel to abort."),
    }
    state_action, prompt = prompts[action]
    _user_state[uid] = {"action": state_action, "step": 1}
    await cb.answer()
    await cb.message.reply(prompt)
