import asyncio
import time
import os
import re
import subprocess
import requests
import tempfile
import shutil
from devgagan import app
from devgagan import sex as gf
import pymongo
from pyrogram import filters
from pyrogram.errors import ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid, PeerIdInvalid, FloodWait, RPCError, ChatAdminRequired
from pyrogram.enums import MessageMediaType
from devgagan.core.func import progress_bar, video_metadata, screenshot, normalize_link
from devgagan.core.mongo import db as settings_db
from pyrogram.types import Message
from config import MONGO_DB as MONGODB_CONNECTION_STRING, LOG_GROUP
import cv2
from telethon import events

ENABLE_MEDIA_LOGGING = os.getenv("ENABLE_MEDIA_LOGGING", "false").strip().lower() in {"1", "true", "yes", "on"}


# ------------- PDF WATERMARK IMPORTS --------------
# Will give after 200 star on my repo or 100+ followers ...
# ------------- PDF WATERMARK IMPORTS --------------

# Define a dictionary to store user chat IDs (assuming this is global/imported elsewhere)
user_chat_ids = {}
# Initialize the dictionary to store user preferences for renaming (assuming this is global/imported elsewhere)
user_rename_preferences = {}
# Initialize the dictionary to store user caption (assuming this is global/imported elsewhere)
user_caption_preferences = {}

def thumbnail(sender):
    return f'{sender}.jpg' if os.path.exists(f'{sender}.jpg') else None

async def _telegram_thumbnail(path, sender):
    """Convert a thumbnail to a small JPEG suitable for Telegram uploads."""
    if not path or not os.path.exists(path):
        return None
    for size, quality in ((320, 8), (160, 12)):
        output = f"{sender}_{time.time_ns()}_thumb.jpg"
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", path,
            "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
            "-frames:v", "1", "-q:v", str(quality), output,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
        if os.path.isfile(output) and os.path.getsize(output) <= 200 * 1024:
            return output
        if os.path.exists(output):
            os.remove(output)
    return path

async def get_custom_thumbnail(sender, temp_dir=None):
    settings = await settings_db.get_data(sender) or {}
    custom_thumb_id = settings.get("thumb")
    if custom_thumb_id:
        try:
            download_args = {"file_name": temp_dir + os.sep} if temp_dir else {}
            custom_path = await app.download_media(custom_thumb_id, **download_args)
            if custom_path and os.path.exists(custom_path):
                prepared = await _telegram_thumbnail(custom_path, sender)
                if prepared != custom_path:
                    os.remove(custom_path)
                return prepared
        except Exception as e:
            print(f"Could not download custom thumbnail: {e}")
    custom_thumb = thumbnail(sender)
    if custom_thumb:
        return await _telegram_thumbnail(custom_thumb, sender)
    return None

async def get_video_thumbnail(client, message, video_path, duration, sender, temp_dir=None):
    """Prefer the user's custom thumbnail, then the source Telegram thumbnail."""
    custom_thumb = await get_custom_thumbnail(sender, temp_dir=temp_dir)
    if custom_thumb:
        return custom_thumb

    media = getattr(message, "video", None) or getattr(message, "document", None)
    source_thumbs = getattr(media, "thumbs", None) or []
    if source_thumbs:
        source_thumb = max(
            source_thumbs,
            key=lambda item: (getattr(item, "width", 0) or 0) * (getattr(item, "height", 0) or 0)
        )
        try:
            download_args = {"file_name": temp_dir + os.sep} if temp_dir else {}
            thumb_path = await client.download_media(source_thumb.file_id, **download_args)
            if thumb_path and os.path.exists(thumb_path):
                prepared = await _telegram_thumbnail(thumb_path, sender)
                if prepared != thumb_path:
                    os.remove(thumb_path)
                return prepared
        except Exception as e:
            print(f"Could not download source thumbnail: {e}")

    return await screenshot(video_path, duration, sender)

def _apply_filename_rules(path, user_id, delete_words, replacements, rename_tag=None):
    directory, filename = os.path.split(path)
    stem, extension = os.path.splitext(filename)
    stem = _remove_configured_words(stem, delete_words)
    for word, replacement in replacements.items():
        stem = stem.replace(word, replacement)
    if rename_tag is None:
        rename_tag = get_user_rename_preference(user_id)
    rename_tag = (rename_tag or "").replace("/", "_").replace("\\", "_").strip()
    if rename_tag:
        stem = f"{stem.strip()} {rename_tag}"
    stem = stem.replace("/", "_").replace("\\", "_").strip(" .") or "file"
    new_path = os.path.join(directory, stem + extension)
    if new_path != path:
        os.replace(path, new_path)
    return new_path

def _remove_configured_words(text, words):
    """Remove configured phrases without depending on capitalization."""
    for word in sorted((str(w) for w in words if str(w)), key=len, reverse=True):
        text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()

async def _copy_to_log_safely(message):
    if not ENABLE_MEDIA_LOGGING:
        return
    try:
        await message.copy(LOG_GROUP)
    except Exception as e:
        # Logging is optional and must not change the result of a successful upload.
        print(f"Non-fatal log-group copy failure: {type(e).__name__}: {e}")

async def _pin_safely(message):
    try:
        await message.pin(both_sides=True)
    except Exception:
        try:
            await message.pin()
        except Exception as e:
            print(f"Non-fatal pin failure: {type(e).__name__}: {e}")

# Helper functions that are missing in the provided snippet but are required:
# load_delete_words, load_replacement_words, get_user_rename_preference, get_user_caption_preference
# Assuming these are defined elsewhere and accessible, as they are used extensively.

async def get_msg(userbot, sender, edit_id, msg_link, i, message):
    msg_link = normalize_link(msg_link)  # normalize telegram.me → t.me
    try:
        saved_settings = await asyncio.wait_for(settings_db.get_data(sender), timeout=10) or {}
    except asyncio.CancelledError:
        try:
            await app.edit_message_text(sender, edit_id, "❌ Processing cancelled.")
        except Exception:
            pass
        raise
    except Exception as e:
        await app.edit_message_text(sender, edit_id, f"❌ Could not load settings: {type(e).__name__}: {e}")
        return False
    if saved_settings.get("chat_id") is not None:
        user_chat_ids[sender] = saved_settings["chat_id"]
    else:
        user_chat_ids.pop(sender, None)
    if "caption" in saved_settings:
        user_caption_preferences[str(sender)] = saved_settings.get("caption") or ""
    if "rename_tag" in saved_settings:
        user_rename_preferences[str(sender)] = saved_settings.get("rename_tag") or ""
    edit = ""
    chat = ""
    file = None # File variable ko initialize kiya
    download_dir = None
    thumb_path = None # Thumbnail path ko initialize kiya
    success = False
    round_message = False
    if "?single" in msg_link:
        msg_link = msg_link.split("?single")[0]
    msg_id = int(msg_link.split("/")[-1]) + int(i)


    if 't.me/c/' in msg_link or 't.me/b/' in msg_link:
        parts = msg_link.split("/")
        if 't.me/b/' not in msg_link:
            chat = int('-100' + str(parts[parts.index('c') + 1])) # topic group/subgroup support enabled
        else:
            chat = msg_link.split("/")[-2]
        file = ""
        try:
            chatx = message.chat.id
            msg = await asyncio.wait_for(userbot.get_messages(chat, msg_id), timeout=30)
            caption = None

            if not msg:
                await app.edit_message_text(sender, edit_id, "❌ Source message was not found or is not accessible to your logged-in account.")
                return False
            if msg.service:
                await app.edit_message_text(sender, edit_id, "❌ This is a Telegram service message and cannot be copied.")
                return False
            if msg.empty:
                await app.edit_message_text(sender, edit_id, "❌ Source message is empty or unavailable to your logged-in account.")
                return False
            if msg.media:
                if msg.media == MessageMediaType.WEB_PAGE:
                    target_chat_id = user_chat_ids.get(chatx, chatx)
                    edit = await app.edit_message_text(sender, edit_id, "Cloning...")
                    devgaganin = await app.send_message(target_chat_id, msg.text.markdown)
                    if msg.pinned_message:
                        await _pin_safely(devgaganin)
                    await _copy_to_log_safely(devgaganin)
                    await edit.delete()
                    return True
            if not msg.media:
                if msg.text:
                    target_chat_id = user_chat_ids.get(chatx, chatx)
                    edit = await app.edit_message_text(sender, edit_id, "Cloning...")
                    devgaganin = await app.send_message(target_chat_id, msg.text.markdown)
                    if msg.pinned_message:
                        await _pin_safely(devgaganin)
                    await _copy_to_log_safely(devgaganin)
                    await edit.delete()
                    return True

            edit = await app.edit_message_text(sender, edit_id, "Trying to Download...")
            download_dir = tempfile.mkdtemp(prefix="devgagan_media_")
            file = await userbot.download_media(
                msg,
                file_name=download_dir + os.sep,
                progress=progress_bar,
                progress_args=("**__Downloading: __**",edit,time.time()))

            custom_rename_tag = get_user_rename_preference(chatx)
            file_dir = os.path.dirname(file)
            original_file_name, original_ext = os.path.splitext(os.path.basename(file))
            file_extension = original_ext.lstrip('.') or 'mp4'
            if file_extension.lower() == 'mov':
                file_extension = 'mp4'

            delete_words = load_delete_words(chatx)
            original_file_name = _remove_configured_words(original_file_name, delete_words)
            replacements = load_replacement_words(chatx)
            for word, replace_word in replacements.items():
                original_file_name = original_file_name.replace(word, replace_word)
            original_file_name = original_file_name.replace('/', '_').replace('\\', '_').strip(' .') or 'file'
            safe_rename_tag = (custom_rename_tag or '').replace('/', '_').replace('\\', '_').strip()
            rename_suffix = f" {safe_rename_tag}" if safe_rename_tag else ""
            new_file_name = os.path.join(file_dir, original_file_name + rename_suffix + "." + file_extension)
            os.rename(file, new_file_name)
            file = new_file_name

            # CODES are hidden

            await edit.edit('Trying to Uplaod ...')

            if msg.media == MessageMediaType.VIDEO and msg.video.mime_type in ["video/mp4", "video/x-matroska"]:

                metadata = video_metadata(file)
                width= metadata['width']
                height= metadata['height']
                duration= metadata['duration']

                if duration <= 300:
                    upload_ok = False
                    try:
                        custom_caption = get_user_caption_preference(sender)
                        final_caption = msg.caption or ''
                        final_caption = _remove_configured_words(final_caption, load_delete_words(sender))
                        for word, replace_word in load_replacement_words(sender).items():
                            final_caption = final_caption.replace(word, replace_word)
                        caption = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else final_caption
                        target_chat_id = user_chat_ids.get(chatx, chatx)
                        short_thumb = await get_video_thumbnail(userbot, msg, file, duration, chatx, temp_dir=download_dir)
                        devgaganin = await app.send_video(chat_id=target_chat_id, video=file, caption=caption, height=height, width=width, duration=duration, thumb=short_thumb, supports_streaming=True, progress=progress_bar, progress_args=('**UPLOADING:**\n', edit, time.time()))
                        if msg.pinned_message:
                            await _pin_safely(devgaganin)
                        await _copy_to_log_safely(devgaganin)
                        upload_ok = True
                        await edit.delete()
                    except PeerIdInvalid as e:
                        await app.edit_message_text(sender, edit_id, f"❌ Telegram could not resolve destination chat `{target_chat_id}`. Check Settings → Chat ID, make sure the ID is correct and the bot is a member with permission to send messages. For delivery to your DM, remove the custom Chat ID.")
                    except Exception as e:
                        await app.edit_message_text(sender, edit_id, f"❌ Video Upload mein anjaan error. Error: `{type(e).__name__}: {str(e)}`")

                    if os.path.exists(file):
                        os.remove(file)
                    if 'short_thumb' in locals() and short_thumb and os.path.exists(short_thumb) and short_thumb != f'{chatx}.jpg':
                        os.remove(short_thumb)
                    if download_dir:
                        shutil.rmtree(download_dir, ignore_errors=True)
                    return upload_ok

                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                final_caption = f"{original_caption}" if custom_caption else f"{original_caption}"
                final_caption = _remove_configured_words(final_caption, delete_words)

                replacements = load_replacement_words(sender)
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)
                caption = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else f"{final_caption}"

                target_chat_id = user_chat_ids.get(chatx, chatx)

                thumb_path = await get_video_thumbnail(userbot, msg, file, duration, chatx, temp_dir=download_dir)
                try:
                    devgaganin = await app.send_video(
                        chat_id=target_chat_id,
                        video=file,
                        caption=caption,
                        supports_streaming=True,
                        height=height,
                        width=width,
                        duration=duration,
                        thumb=thumb_path,
                        progress=progress_bar,
                        progress_args=(
                        '**__Uploading...__**',
                        edit,
                        time.time()
                        )
                       )
                    if msg.pinned_message:
                        await _pin_safely(devgaganin)
                    await _copy_to_log_safely(devgaganin)
                    success = True

                except ChatAdminRequired as e:
                    # ✅ Theek error 1: Ab yeh sirf tabhi dikhega jab sach mein permission nahi hogi
                    await app.edit_message_text(sender, edit_id, "❌ Error: Bot ko target chat/channel mein Admin banao, ya Chat ID galat hai.")
                except PeerIdInvalid as e:
                    await app.edit_message_text(sender, edit_id, f"❌ Telegram could not resolve destination chat `{target_chat_id}`. Check Settings → Chat ID, make sure the ID is correct and the bot is a member with permission to send messages. For delivery to your DM, remove the custom Chat ID.")
                except (RPCError, FloodWait) as e:
                    # ✅ Theek error 2: Badi file ya network ki samasya ko pehchana gaya
                    await app.edit_message_text(sender, edit_id, f"❌ Upload Failed! File 4 GB se badi hai, ya network/Userbot mein dikkat hai. Error: `{type(e).__name__}: {str(e)}`")
                except Exception as e:
                    # Anjaan errors ke liye
                    await app.edit_message_text(sender, edit_id, f"❌ Video Upload mein anjaan error. Error: `{type(e).__name__}: {str(e)}`")

                # ⚠️ FIX: Safe File Cleanup (Downloaded file aur Thumbnail dono delete honge)
                if os.path.exists(file):
                    os.remove(file)
                # Thumbnail ko delete karein (Agar woh temp file hai aur user ka set kiya hua thumbnail nahi hai)
                if 'thumb_path' in locals() and thumb_path and os.path.exists(thumb_path) and thumb_path != f'{chatx}.jpg':
                    os.remove(thumb_path)

            elif msg.media == MessageMediaType.PHOTO:
                await edit.edit("**Uploading photo...")
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                final_caption = f"{original_caption}" if custom_caption else f"{original_caption}"
                final_caption = _remove_configured_words(final_caption, delete_words)
                replacements = load_replacement_words(sender)
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)
                caption = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else f"{final_caption}"

                target_chat_id = user_chat_ids.get(sender, sender)
                devgaganin = await app.send_photo(chat_id=target_chat_id, photo=file, caption=caption)
                if msg.pinned_message:
                    await _pin_safely(devgaganin)
                await _copy_to_log_safely(devgaganin)
                success = True

                # ⚠️ FIX: Photo upload ke dauran temporary file delete karein
                if os.path.exists(file):
                    os.remove(file)

            else:
                thumb_path = thumbnail(chatx)
                if not thumb_path:
                    try:
                        thumb_path = await get_custom_thumbnail(chatx, temp_dir=download_dir)
                    except Exception as e:
                        print(f"Could not prepare custom document thumbnail: {e}")
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                final_caption = f"{original_caption}" if custom_caption else f"{original_caption}"
                final_caption = _remove_configured_words(final_caption, delete_words)
                replacements = load_replacement_words(chatx)
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)
                caption = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else f"{final_caption}"
                file_extension = file_extension.lower() # fixed all video document files sent as video files
                video_extensions = {
    'mkv', 'mp4', 'webm', 'mpe4', 'mpeg', 'ts', 'avi', 'flv', 'mov',
    'm4v', '3gp', '3g2', 'wmv', 'vob', 'ogv', 'ogx', 'qt', 'f4v',
    'f4p', 'f4a', 'f4b', 'dat', 'rm', 'rmvb', 'asf', 'amv', 'divx'
                }

                target_chat_id = user_chat_ids.get(chatx, chatx)
                try:
                    if file_extension in video_extensions:
                        metadata = video_metadata(file)
                        width= metadata['width']
                        height= metadata['height']
                        duration= metadata['duration']
                        thumb_path = await get_video_thumbnail(userbot, msg, file, duration, chatx, temp_dir=download_dir)
                        devgaganin = await app.send_video(
                            chat_id=target_chat_id,
                            video=file,
                            caption=caption,
                            supports_streaming=True,
                            height=height,
                            width=width,
                            duration=duration,
                            thumb=thumb_path,
                            progress=progress_bar,
                            progress_args=(
                                '**Uploading...**',
                                edit,
                                time.time()
                            )
                        )
                    else:
                        devgaganin = await app.send_document(
                            chat_id=target_chat_id,
                            document=file,
                            caption=caption,
                            thumb=thumb_path,
                            progress=progress_bar,
                            progress_args=(
                                '**Uploading...**',
                                edit,
                                time.time()
                            )
                        )

                    await _copy_to_log_safely(devgaganin)
                    success = True

                except ChatAdminRequired as e:
                    # ✅ Theek error 1: Admin ya Chat ID ka error
                    await app.edit_message_text(sender, edit_id, "❌ Error: Bot ko target chat/channel mein Admin banao, ya Chat ID galat hai.")
                except PeerIdInvalid as e:
                    await app.edit_message_text(sender, edit_id, f"❌ Telegram could not resolve destination chat `{target_chat_id}`. Check Settings → Chat ID, make sure the ID is correct and the bot is a member with permission to send messages. For delivery to your DM, remove the custom Chat ID.")
                except (RPCError, FloodWait) as e:
                    # ✅ Theek error 2: Badi file ya network ki samasya
                    await app.edit_message_text(sender, edit_id, f"❌ Upload Failed! File 4 GB se badi hai, ya network/Userbot mein dikkat hai. Error: `{type(e).__name__}: {str(e)}`")
                except Exception as e:
                    # Anjaan errors ke liye
                    await app.edit_message_text(sender, edit_id, f"❌ Document Upload mein anjaan error. Error: `{type(e).__name__}: {str(e)}`")

                # ⚠️ FIX: Safe File Cleanup (Downloaded file aur Thumbnail dono delete honge)
                if os.path.exists(file):
                    os.remove(file)
                # Thumbnail ko delete karein (Agar woh temp file hai aur user ka set kiya hua thumbnail nahi hai)
                if 'thumb_path' in locals() and thumb_path and os.path.exists(thumb_path) and thumb_path != f'{chatx}.jpg':
                    os.remove(thumb_path)

            await edit.delete()
            if download_dir:
                shutil.rmtree(download_dir, ignore_errors=True)
                download_dir = None
            return success

        except asyncio.CancelledError:
            if file and os.path.exists(file):
                os.remove(file)
            if thumb_path and os.path.exists(thumb_path) and thumb_path != f'{chatx}.jpg':
                os.remove(thumb_path)
            if download_dir:
                shutil.rmtree(download_dir, ignore_errors=True)
            try:
                await app.edit_message_text(sender, edit_id, "❌ Processing cancelled.")
            except Exception:
                pass
            raise
        except (ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid):
            await app.edit_message_text(sender, edit_id, "Have you joined the channel?")

            # Agar error aane se pehle download ho chuka hai, to use delete karein
            if file and os.path.exists(file):
                os.remove(file)
            if 'thumb_path' in locals() and thumb_path and os.path.exists(thumb_path) and thumb_path != f'{chatx}.jpg':
                os.remove(thumb_path)

            if download_dir:
                shutil.rmtree(download_dir, ignore_errors=True)
            return False
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')

            # Agar error aane se pehle download ho chuka hai, to use delete karein
            if file and os.path.exists(file):
                os.remove(file)
            if download_dir:
                shutil.rmtree(download_dir, ignore_errors=True)
            if 'thumb_path' in locals() and thumb_path and os.path.exists(thumb_path) and thumb_path != f'{chatx}.jpg':
                os.remove(thumb_path)
            return False

    else:
        edit = await app.edit_message_text(sender, edit_id, "Cloning...")
        try:
            parts = msg_link.split("/")
            # Regular public channel:  t.me/username/12       → parts[-2] = "username"
            # Public group with topic: t.me/username/6/12     → parts[-2] = "6" (topic id, digit)
            #                                                  → parts[-3] = "username"
            if parts[-2].isdigit():
                chat = parts[-3]  # topic group — go one level up to get username
            else:
                chat = parts[-2]  # regular channel/group

            if userbot is not None:
                copied = await copy_message_with_chat_id(userbot, sender, chat, msg_id, upload_client=app, edit_id=edit_id)
            else:
                copied = await copy_message_with_chat_id(app, sender, chat, msg_id, edit_id=edit_id)
            if not copied:
                return False
            await edit.delete()
            return True
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\nError: {type(e).__name__}: {str(e)}')
            return False


async def copy_message_with_chat_id(client, sender, chat_id, message_id, upload_client=None, edit_id=None):
    # client      = used to GET and DOWNLOAD the message (bot or userbot)
    # upload_client = used to SEND/UPLOAD to user (always bot/app); defaults to client if not set
    if upload_client is None:
        upload_client = client
    target_chat_id = user_chat_ids.get(sender, sender)
    downloaded_file = None
    downloaded_thumb = None
    filename_rules_applied = False
    download_dir = tempfile.mkdtemp(prefix="devgagan_media_")

    try:
        msg = await asyncio.wait_for(client.get_messages(chat_id, message_id), timeout=30)

        if not msg or msg.empty:
            if edit_id is not None:
                await upload_client.edit_message_text(sender, edit_id, "❌ Source message was not found or is empty.")
            return False

        custom_caption = get_user_caption_preference(sender)
        original_caption = msg.caption if msg.caption else ''
        final_caption = original_caption

        delete_words = load_delete_words(sender)
        final_caption = _remove_configured_words(final_caption, delete_words)

        replacements = load_replacement_words(sender)
        for word, replace_word in replacements.items():
            final_caption = final_caption.replace(word, replace_word)

        caption = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else final_caption
        rename_tag = get_user_rename_preference(sender)
        needs_filename_edit = bool(delete_words or replacements or rename_tag)
        is_video_document = (
            msg.media == MessageMediaType.DOCUMENT
            and (getattr(msg.document, "mime_type", "") or "").startswith("video/")
        )

        result = None

        if msg.media:
            # Step 1: Try fast path using file_id (instant, no download)
            try:
                if msg.media == MessageMediaType.VIDEO or is_video_document:
                    # Download videos so a frame thumbnail can be generated;
                    # forwarding only the file_id reuses Telegram's old thumbnail.
                    downloaded_file = await client.download_media(msg, file_name=download_dir + os.sep)
                    if not downloaded_file:
                        raise RuntimeError("Video download failed; cannot generate thumbnail")
                    downloaded_file = _apply_filename_rules(
                        downloaded_file, sender, delete_words, replacements, rename_tag
                    )
                    filename_rules_applied = True
                    metadata = video_metadata(downloaded_file)
                    thumb = await get_video_thumbnail(client, msg, downloaded_file, metadata['duration'], sender, temp_dir=download_dir)
                    if thumb and thumb != f'{sender}.jpg':
                        downloaded_thumb = thumb
                    result = await upload_client.send_video(
                        target_chat_id, downloaded_file, caption=caption,
                        width=metadata['width'], height=metadata['height'],
                        duration=metadata['duration'], thumb=thumb,
                        supports_streaming=True
                    )
                    if thumb and os.path.exists(thumb) and thumb != f'{sender}.jpg':
                        os.remove(thumb)
                elif msg.media == MessageMediaType.DOCUMENT:
                    if needs_filename_edit:
                        downloaded_file = await client.download_media(msg, file_name=download_dir + os.sep)
                        if not downloaded_file:
                            raise RuntimeError("Document download failed")
                        downloaded_file = _apply_filename_rules(
                            downloaded_file, sender, delete_words, replacements, rename_tag
                        )
                        filename_rules_applied = True
                        result = await upload_client.send_document(target_chat_id, downloaded_file, caption=caption)
                    else:
                        result = await upload_client.send_document(target_chat_id, msg.document.file_id, caption=caption)
                elif msg.media == MessageMediaType.PHOTO:
                    result = await upload_client.send_photo(target_chat_id, msg.photo.file_id, caption=caption)
                elif msg.media == MessageMediaType.AUDIO:
                    result = await upload_client.send_audio(target_chat_id, msg.audio.file_id, caption=caption)
                elif msg.media == MessageMediaType.VOICE:
                    result = await upload_client.send_voice(target_chat_id, msg.voice.file_id, caption=caption)
                elif msg.media == MessageMediaType.VIDEO_NOTE:
                    result = await upload_client.send_video_note(target_chat_id, msg.video_note.file_id)
                elif msg.media == MessageMediaType.STICKER:
                    result = await upload_client.send_sticker(target_chat_id, msg.sticker.file_id)
                elif msg.media == MessageMediaType.ANIMATION:
                    result = await upload_client.send_animation(target_chat_id, msg.animation.file_id, caption=caption)
                else:
                    result = await upload_client.copy_message(target_chat_id, chat_id, message_id)
            except Exception:
                # Step 2: Fallback — download via client, upload via upload_client
                # This handles copy-protected content
                try:
                    if not downloaded_file or not os.path.exists(downloaded_file):
                        downloaded_file = await client.download_media(msg, file_name=download_dir + os.sep)
                    if downloaded_file:
                        if msg.media == MessageMediaType.VIDEO or is_video_document:
                            if not filename_rules_applied:
                                downloaded_file = _apply_filename_rules(
                                    downloaded_file, sender, delete_words, replacements, rename_tag
                                )
                                filename_rules_applied = True
                            metadata = video_metadata(downloaded_file)
                            thumb = await get_video_thumbnail(client, msg, downloaded_file, metadata['duration'], sender, temp_dir=download_dir)
                            result = await upload_client.send_video(
                                target_chat_id, downloaded_file, caption=caption,
                                width=metadata['width'], height=metadata['height'],
                                duration=metadata['duration'], thumb=thumb, supports_streaming=True
                            )
                            if thumb and os.path.exists(thumb) and thumb != f'{sender}.jpg':
                                os.remove(thumb)
                        elif msg.media == MessageMediaType.PHOTO:
                            result = await upload_client.send_photo(target_chat_id, downloaded_file, caption=caption)
                        else:
                            if not filename_rules_applied:
                                downloaded_file = _apply_filename_rules(
                                    downloaded_file, sender, delete_words, replacements, rename_tag
                                )
                                filename_rules_applied = True
                            result = await upload_client.send_document(target_chat_id, downloaded_file, caption=caption)
                except Exception as e2:
                    error_text = f"❌ Failed to save content.\n\nError: `{type(e2).__name__}: {e2}`"
                    if edit_id is not None:
                        await upload_client.edit_message_text(sender, edit_id, error_text)
                    else:
                        await upload_client.send_message(sender, error_text)
                    return False
        else:
            # Text only message
            try:
                result = await upload_client.copy_message(target_chat_id, chat_id, message_id)
            except Exception:
                if msg.text:
                    result = await upload_client.send_message(target_chat_id, msg.text.markdown)

        if result:
            try:
                await _copy_to_log_safely(result)
            except Exception:
                pass
            if msg.pinned_message:
                try:
                    await result.pin(both_sides=True)
                except Exception:
                    try:
                        await result.pin()
                    except Exception:
                        pass

        if not result and edit_id is not None:
            await upload_client.edit_message_text(sender, edit_id, "❌ No copy or upload result was returned. The source may be inaccessible or unsupported.")
        return bool(result)
    except asyncio.CancelledError:
        if edit_id is not None:
            try:
                await upload_client.edit_message_text(sender, edit_id, "❌ Processing cancelled.")
            except Exception:
                pass
        raise
    except Exception as e:
        if edit_id is not None:
            try:
                await upload_client.edit_message_text(sender, edit_id, f"❌ Error: `{type(e).__name__}: {e}`")
            except Exception:
                pass
        else:
            await upload_client.send_message(sender, f"❌ Error: `{str(e)}`")
        return False
    finally:
        if downloaded_thumb and os.path.exists(downloaded_thumb):
            try:
                os.remove(downloaded_thumb)
            except FileNotFoundError:
                pass
        if downloaded_file and os.path.exists(downloaded_file):
            try:
                os.remove(downloaded_file)
            except FileNotFoundError:
                pass
        shutil.rmtree(download_dir, ignore_errors=True)

# -------------- FFMPEG CODES ---------------

# ------------------------ Button Mode Editz FOR SETTINGS ----------------------------

# MongoDB database name and collection name
DB_NAME = "smart_users"
COLLECTION_NAME = "super_user"

# Establish a connection to MongoDB
mongo_client = pymongo.MongoClient(MONGODB_CONNECTION_STRING, serverSelectionTimeoutMS=5000)
db = mongo_client[DB_NAME]
collection = db[COLLECTION_NAME]
rules_collection = mongo_client["user_data"]["users_data_db"]

def load_authorized_users():
    """
    Load authorized user IDs from the MongoDB collection
    """
    authorized_users = set()
    for user_doc in collection.find():
        if "user_id" in user_doc:
            authorized_users.add(user_doc["user_id"])
    return authorized_users

def save_authorized_users(authorized_users):
    """
    Replace only authorization-list documents, preserving per-user settings documents.
    """
    collection.delete_many({"user_id": {"$exists": True}})
    for user_id in authorized_users:
        collection.insert_one({"user_id": user_id})

try:
    SUPER_USERS = load_authorized_users()
except Exception:
    SUPER_USERS = set()


def load_delete_words(user_id):
    """
    Load delete words for a specific user from MongoDB
    """
    try:
        # The settings panel stores its canonical value in this collection.
        settings_data = rules_collection.find_one({"_id": user_id}, {"clean_words": 1})
        if settings_data is not None and "clean_words" in settings_data:
            configured = settings_data.get("clean_words") or []
            if isinstance(configured, str):
                configured = [configured]
            return {str(word).strip() for word in configured if str(word).strip()}
    except Exception as e:
        print(f"Error loading panel remove words: {e}")
    try:
        words_data = collection.find_one({"_id": user_id})
        if words_data:
            configured = words_data.get("delete_words", []) or []
            if isinstance(configured, str):
                configured = [configured]
            return {str(word).strip() for word in configured if str(word).strip()}
        else:
            return set()
    except Exception as e:
        print(f"Error loading delete words: {e}")
        return set()

def save_delete_words(user_id, delete_words):
    """
    Save delete words for a specific user to MongoDB
    """
    try:
        collection.update_one(
            {"_id": user_id},
            {"$set": {"delete_words": list(delete_words)}},
            upsert=True
        )
    except Exception as e:
        print(f"Error saving delete words: {e}")

def load_replacement_words(user_id):
    try:
        settings_data = rules_collection.find_one({"_id": user_id}, {"replacement_words": 1})
        if settings_data is not None and "replacement_words" in settings_data:
            return settings_data.get("replacement_words") or {}
        words_data = collection.find_one({"_id": user_id})
        if words_data:
            return words_data.get("replacement_words", {})
        else:
            return {}
    except Exception as e:
        print(f"Error loading replacement words: {e}")
        return {}

def save_replacement_words(user_id, replacements):
    try:
        collection.update_one(
            {"_id": user_id},
            {"$set": {"replacement_words": replacements}},
            upsert=True
        )
    except Exception as e:
        print(f"Error saving replacement words: {e}")

# Function to load user session from MongoDB
def load_user_session(sender_id):
    user_data = collection.find_one({"user_id": sender_id})
    if user_data:
        return user_data.get("session")
    else:
        return None  # Or handle accordingly if session doesn't exist

# Function to handle the /setrename command
async def set_rename_command(user_id, custom_rename_tag):
    # Update the user_rename_preferences dictionary
    user_rename_preferences[str(user_id)] = custom_rename_tag
    collection.update_one({"_id": user_id}, {"$set": {"rename_tag": custom_rename_tag}}, upsert=True)

# Function to get the user's custom renaming preference
def get_user_rename_preference(user_id):
    # Retrieve the user's custom renaming tag if set, or default to 'Thank God'
    value = user_rename_preferences.get(str(user_id))
    if value is not None:
        return value
    try:
        settings_data = rules_collection.find_one({"_id": user_id}, {"rename_tag": 1})
        if settings_data is not None and "rename_tag" in settings_data:
            value = settings_data.get("rename_tag") or ""
            user_rename_preferences[str(user_id)] = value
            return value
        value = (collection.find_one({"_id": user_id}) or {}).get("rename_tag")
        if value is not None:
            user_rename_preferences[str(user_id)] = value
            return value
    except Exception:
        pass
    return ''

# Function to set custom caption preference
async def set_caption_command(user_id, custom_caption):
    # Update the user_caption_preferences dictionary
    user_caption_preferences[str(user_id)] = custom_caption
    collection.update_one({"_id": user_id}, {"$set": {"caption": custom_caption}}, upsert=True)

# Function to get the user's custom caption preference
def get_user_caption_preference(user_id):
    # Retrieve the user's custom caption if set, or default to an empty string
    value = user_caption_preferences.get(str(user_id))
    if value is not None:
        return value
    try:
        settings_data = rules_collection.find_one({"_id": user_id}, {"caption": 1})
        if settings_data is not None and "caption" in settings_data:
            value = settings_data.get("caption") or ""
            user_caption_preferences[str(user_id)] = value
            return value
        value = (collection.find_one({"_id": user_id}) or {}).get("caption")
        if value is not None:
            user_caption_preferences[str(user_id)] = value
            return value
    except Exception:
        pass
    return ''


@gf.on(events.NewMessage(incoming=True, pattern='/settings_legacy'))
async def settings_command(event):
    await event.respond("This legacy settings panel is disabled. Please use /settings in your private chat.")

@gf.on(events.CallbackQuery)
async def callback_query_handler(event):
    legacy_buttons = {
        b'setchat', b'setrename', b'setcaption', b'setreplacement', b'addsession',
        b'delete', b'logout', b'setthumb', b'reset', b'remthumb',
    }
    if event.data in legacy_buttons:
        await event.answer(
            "This legacy settings panel is disabled. Please use /settings in your private chat.",
            alert=True,
        )
