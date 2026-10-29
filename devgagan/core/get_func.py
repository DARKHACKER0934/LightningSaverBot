# devggn — ultra-fast save engine
# 1) file_id fast path (zero download) for all media
# 2) persistent file_id cache → repeat saves are instant
# 3) in-process cv2 thumbnails + cached thumb files
# 4) ffprobe metadata (no frame decode)
# 5) FloodWait-safe uploads with auto retry

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time

from pyrogram import raw, types
from pyrogram.errors import (
    ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid,
    FloodWait, PeerIdInvalid, RPCError, ChatAdminRequired,
)
from pyrogram.enums import MessageMediaType

from devgagan import app
from devgagan.core.func import (
    progress_bar, video_metadata, normalize_link, _extract_frame_cv2,
)
from devgagan.core.speedhacks import fast_download, patch_upload_speed
from devgagan.core.cache import file_id_cache, thumb_cache
from devgagan.core.mongo import db as settings_db
from config import LOG_GROUP

patch_upload_speed()

ENABLE_MEDIA_LOGGING = os.getenv("ENABLE_MEDIA_LOGGING", "false").strip().lower() in {"1", "true", "yes", "on"}

THUMB_DIR = "/app/downloads/thumbs"
os.makedirs(THUMB_DIR, exist_ok=True)

# In-memory per-user runtime state (fast lookups, no DB)
user_chat_ids = {}
user_rename_preferences = {}
user_caption_preferences = {}

VIDEO_EXTENSIONS = {
    'mkv', 'mp4', 'webm', 'mpe4', 'mpeg', 'ts', 'avi', 'flv', 'mov',
    'm4v', '3gp', '3g2', 'wmv', 'vob', 'ogv', 'ogx', 'qt', 'f4v',
    'f4p', 'f4a', 'f4b', 'dat', 'rm', 'rmvb', 'asf', 'amv', 'divx',
}

DOWNLOAD_TIMEOUT = 30


# ── Cached settings helpers (all async, motor-backed, TTL cached) ─────────────

async def _settings(sender):
    return await settings_db.cached_get_data(sender)


def thumbnail(sender):
    return f'{sender}.jpg' if os.path.exists(f'{sender}.jpg') else None


async def load_delete_words(user_id):
    data = await _settings(user_id)
    configured = data.get("clean_words") or data.get("delete_words") or []
    if isinstance(configured, str):
        configured = [configured]
    return {str(word).strip() for word in configured if str(word).strip()}


async def load_replacement_words(user_id):
    data = await _settings(user_id)
    rules = data.get("replacement_words") or {}
    return rules if isinstance(rules, dict) else {}


async def get_user_rename_preference(user_id):
    value = user_rename_preferences.get(str(user_id))
    if value is not None:
        return value
    data = await _settings(user_id)
    value = data.get("rename_tag") or ""
    user_rename_preferences[str(user_id)] = value
    return value


async def get_user_caption_preference(user_id):
    value = user_caption_preferences.get(str(user_id))
    if value is not None:
        return value
    data = await _settings(user_id)
    value = data.get("caption") or ""
    user_caption_preferences[str(user_id)] = value
    return value


async def set_rename_command(user_id, custom_rename_tag):
    user_rename_preferences[str(user_id)] = custom_rename_tag
    await settings_db.set_rename_tag(user_id, custom_rename_tag)


async def set_caption_command(user_id, custom_caption):
    user_caption_preferences[str(user_id)] = custom_caption
    await settings_db.set_caption(user_id, custom_caption)


async def save_delete_words(user_id, words):
    await settings_db.set_clean_words(user_id, list(words))


async def save_replacement_words(user_id, replacements):
    await settings_db.set_replace_words(user_id, replacements)


# ── Text / filename helpers ───────────────────────────────────────────────────

def _remove_configured_words(text, words):
    for word in sorted((str(w) for w in words if str(w)), key=len, reverse=True):
        text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()


async def _build_caption(msg, sender):
    custom_caption = await get_user_caption_preference(sender)
    # Use .markdown to preserve the original formatting (bold/italic/links)
    try:
        final_caption = msg.caption.markdown if msg.caption else ''
    except Exception:
        final_caption = msg.caption or ''
    final_caption = _remove_configured_words(final_caption, await load_delete_words(sender))
    for word, replace_word in (await load_replacement_words(sender)).items():
        final_caption = final_caption.replace(word, replace_word)
    return f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else final_caption


async def _apply_filename_rules(path, sender):
    directory, filename = os.path.split(path)
    stem, extension = os.path.splitext(filename)
    stem = _remove_configured_words(stem, await load_delete_words(sender))
    for word, replacement in (await load_replacement_words(sender)).items():
        stem = stem.replace(word, replacement)
    rename_tag = (await get_user_rename_preference(sender) or "").replace("/", "_").replace("\\", "_").strip()
    if rename_tag:
        stem = f"{stem.strip()} {rename_tag}"
    stem = stem.replace("/", "_").replace("\\", "_").strip(" .") or "file"
    new_path = os.path.join(directory, stem + extension)
    if new_path != path:
        os.replace(path, new_path)
    return new_path


# ── Media classification ──────────────────────────────────────────────────────

def media_kind(msg):
    if msg.media == MessageMediaType.VIDEO:
        return "video"
    if msg.media == MessageMediaType.PHOTO:
        return "photo"
    if msg.media == MessageMediaType.AUDIO:
        return "audio"
    if msg.media == MessageMediaType.VOICE:
        return "voice"
    if msg.media == MessageMediaType.VIDEO_NOTE:
        return "video_note"
    if msg.media == MessageMediaType.STICKER:
        return "sticker"
    if msg.media == MessageMediaType.ANIMATION:
        return "animation"
    if msg.media == MessageMediaType.DOCUMENT:
        return "video_doc" if (msg.document.mime_type or "").startswith("video/") else "document"
    return None


def _media_of(msg):
    for kind in ("audio", "document", "photo", "sticker", "animation", "video", "voice", "video_note"):
        m = getattr(msg, kind, None)
        if m is not None:
            return m
    return None


def _ext_for(client, media):
    mime = getattr(media, "mime_type", "") or ""
    try:
        return client.guess_extension(mime) or "bin"
    except Exception:
        return "bin"


def media_file_id(msg):
    m = getattr(msg, msg.media.value if msg.media else None, None)
    if m is None:
        if msg.media == MessageMediaType.DOCUMENT:
            m = msg.document
        elif msg.media == MessageMediaType.PHOTO:
            m = msg.photo
    return getattr(m, "file_id", None)


def msg_size(msg):
    for attr in ("video", "document", "audio", "photo", "voice", "animation", "video_note", "sticker"):
        m = getattr(msg, attr, None)
        if m is not None:
            return getattr(m, "file_size", None)
    return None


def result_file_id(result, kind):
    if not result:
        return None
    m = getattr(result, kind if kind != "video_doc" else "document", None)
    if m is None and kind in ("video", "video_doc"):
        m = getattr(result, "video", None) or getattr(result, "document", None)
    return getattr(m, "file_id", None)


async def send_by_kind(cli, target, kind, source, caption=None):
    """Send media by file_id (instant, zero download)."""
    if kind == "video":
        return await cli.send_video(target, source, caption=caption)
    if kind in ("video_doc", "document"):
        return await cli.send_document(target, source, caption=caption)
    if kind == "photo":
        return await cli.send_photo(target, source, caption=caption)
    if kind == "audio":
        return await cli.send_audio(target, source, caption=caption)
    if kind == "voice":
        return await cli.send_voice(target, source)
    if kind == "video_note":
        return await cli.send_video_note(target, source)
    if kind == "sticker":
        return await cli.send_sticker(target, source)
    if kind == "animation":
        return await cli.send_animation(target, source, caption=caption)
    return None


# ── Thumbnails (fast, cached) ─────────────────────────────────────────────────

async def _resize_thumb_inplace(path, max_size=320):
    """Compress a thumbnail to a small JPEG using in-process OpenCV."""
    out = path + ".cv2.jpg"
    try:
        ok = await asyncio.get_running_loop().run_in_executor(
            None, _extract_frame_cv2, path, out, None, max_size
        )
        if ok and os.path.isfile(out) and os.path.getsize(out) <= 200 * 1024:
            if os.path.exists(path) and path != out:
                os.remove(path)
            return out
        if os.path.exists(out):
            os.remove(out)
    except Exception:
        pass
    return path


def _thumb_file(cache_key):
    digest = hashlib.sha1(cache_key.encode()).hexdigest()[:32]
    return os.path.join(THUMB_DIR, digest + ".jpg")


async def _cached_shot(cache_key, video, duration):
    key = f"shot:{cache_key}"
    cached = await thumb_cache.get(key)
    if cached and os.path.isfile(cached):
        return cached
    out = _thumb_file(cache_key)
    seek_sec = float(duration) / 2 if duration else None
    try:
        ok = await asyncio.get_running_loop().run_in_executor(
            None, _extract_frame_cv2, video, out, seek_sec, 320
        )
    except Exception:
        ok = False
    if ok and os.path.isfile(out) and os.path.getsize(out) > 0:
        await thumb_cache.set(key, out)
        return out
    if os.path.exists(out):
        os.remove(out)
    return None


async def get_custom_thumbnail(sender):
    key = f"custom:{sender}"
    cached = await thumb_cache.get(key)
    if cached and os.path.isfile(cached):
        return cached
    settings = await _settings(sender)
    custom_thumb_id = settings.get("thumb")
    if custom_thumb_id:
        try:
            path = await app.download_media(custom_thumb_id, file_name=THUMB_DIR + os.sep)
            if path and os.path.exists(path):
                prepared = await _resize_thumb_inplace(path)
                await thumb_cache.set(key, prepared)
                return prepared
        except Exception as e:
            print(f"Could not download custom thumbnail: {e}")
    return None


async def get_video_thumbnail(client, msg, video_path, duration, sender, cache_key):
    """Custom thumb → cached generated thumb → source thumbs → cv2 screenshot."""
    custom_thumb = await get_custom_thumbnail(sender)
    if custom_thumb:
        return custom_thumb

    key = f"thumb:{cache_key}"
    cached = await thumb_cache.get(key)
    if cached and os.path.isfile(cached):
        return cached

    media = getattr(msg, "video", None) or getattr(msg, "document", None)
    source_thumbs = getattr(media, "thumbs", None) or []
    if source_thumbs:
        source_thumb = max(
            source_thumbs,
            key=lambda item: (getattr(item, "width", 0) or 0) * (getattr(item, "height", 0) or 0)
        )
        try:
            thumb_path = await client.download_media(source_thumb.file_id, file_name=THUMB_DIR + os.sep)
            if thumb_path and os.path.exists(thumb_path):
                prepared = await _resize_thumb_inplace(thumb_path)
                if os.path.isfile(prepared):
                    await thumb_cache.set(key, prepared)
                    return prepared
        except Exception as e:
            print(f"Could not download source thumbnail: {e}")

    shot = await _cached_shot(cache_key, video_path, duration)
    if shot:
        await thumb_cache.set(key, shot)
    return shot


# ── Upload with FloodWait retry ───────────────────────────────────────────────

async def _flood_retry(coro_factory, edit, retries=3):
    for attempt in range(retries):
        try:
            return await coro_factory()
        except FloodWait as fw:
            if attempt == retries - 1:
                raise
            wait = min(fw.x, 120)
            if edit is not None:
                try:
                    await edit.edit(f"⚠️ **Flood limit hit!** Auto-retrying in `{wait}s`...")
                except Exception:
                    pass
            await asyncio.sleep(wait + 2)
    return None


async def _upload_video(cli, target, file, caption, meta, thumb, edit):
    async def send():
        return await cli.send_video(
            chat_id=target,
            video=file,
            caption=caption,
            supports_streaming=True,
            height=meta['height'],
            width=meta['width'],
            duration=meta['duration'],
            thumb=thumb,
            progress=progress_bar,
            progress_args=("🚀 **UPLOADING:**\n", edit, time.time()),
        )

    return await _flood_retry(send, edit)


async def _upload_document(cli, target, file, caption, thumb, edit):
    async def send():
        return await cli.send_document(
            chat_id=target,
            document=file,
            caption=caption,
            thumb=thumb,
            progress=progress_bar,
            progress_args=("🚀 **UPLOADING:**\n", edit, time.time()),
        )

    return await _flood_retry(send, edit)


async def _copy_to_log_safely(message):
    if not ENABLE_MEDIA_LOGGING:
        return
    try:
        await message.copy(LOG_GROUP)
    except Exception as e:
        print(f"Non-fatal log-group copy failure: {type(e).__name__}: {e}")


async def get_topic_message(client, chat, topic_id, msg_id):
    """Fetch a forum-topic message by its TOPIC-LOCAL id (as shown in links)."""
    peer = await client.resolve_peer(chat)
    r = await client.invoke(
        raw.functions.messages.GetReplies(
            peer=peer,
            msg_id=topic_id,
            offset_id=msg_id + 1,
            offset_date=0,
            add_offset=0,
            limit=30,
            max_id=0,
            min_id=0,
            hash=0,
        )
    )
    users = {u.id: u for u in r.users}
    chats = {c.id: c for c in r.chats}
    for m in r.messages:
        if getattr(m, "id", None) == msg_id:
            return await types.Message._parse(client, m, users, chats, replies=0)
    return None


async def _pin_safely(message):
    try:
        await message.pin(both_sides=True)
    except Exception:
        try:
            await message.pin()
        except Exception as e:
            print(f"Non-fatal pin failure: {type(e).__name__}: {e}")


# ── Shared download → upload pipeline ─────────────────────────────────────────

async def _download_upload_media(client, upload_client, sender, target, msg, kind, caption, edit, download_dir, cache_key):
    """Full pipeline with speed optimizations. Returns (ok, result)."""
    file = None
    thumb = None
    try:
        if edit is not None:
            await edit.edit("📥 **DOWNLOADING...**\n\n" + "░" * 12)
        media = _media_of(msg)
        original_name = getattr(media, "file_name", "") or f"media_{int(time.time())}.{_ext_for(client, media) if media is not None else 'bin'}"
        safe_name = os.path.basename(original_name.replace("/", "_").replace("\\", "_")) or "file"
        file = await fast_download(
            client, msg, os.path.join(download_dir, safe_name),
            progress=progress_bar,
            progress_args=("📥 **DOWNLOADING:**\n", edit, time.time()),
        )
        if not file:
            raise RuntimeError("Download failed")

        file = await _apply_filename_rules(file, sender)

        if kind in ("video", "video_doc"):
            meta = await video_metadata(file)
            thumb = await get_video_thumbnail(client, msg, file, meta['duration'], sender, cache_key)
            result = await _upload_video(upload_client, target, file, caption, meta, thumb, edit)
        elif kind == "photo":
            result = await upload_client.send_photo(target, file, caption=caption)
        elif kind == "sticker":
            result = await upload_client.send_sticker(target, file)
        elif kind == "animation":
            result = await upload_client.send_animation(target, file, caption=caption)
        elif kind == "audio":
            result = await upload_client.send_audio(target, file, caption=caption)
        elif kind == "voice":
            result = await upload_client.send_voice(target, file)
        elif kind == "video_note":
            result = await upload_client.send_video_note(target, file)
        else:
            thumb = await get_custom_thumbnail(sender)
            result = await _upload_document(upload_client, target, file, caption, thumb, edit)

        if result:
            dest_id = result_file_id(result, kind)
            if dest_id:
                await file_id_cache.set(cache_key, {"kind": kind, "file_id": dest_id})
            if msg.pinned_message:
                await _pin_safely(result)
            await _copy_to_log_safely(result)
            return True, result
        return False, None
    finally:
        if file and os.path.exists(file):
            try:
                os.remove(file)
            except OSError:
                pass
        if thumb and os.path.isfile(thumb) and THUMB_DIR not in thumb and thumb != f'{sender}.jpg':
            try:
                os.remove(thumb)
            except OSError:
                pass


# ── Public link path (bot can read the channel) ──────────────────────────────

async def copy_message_with_chat_id(client, sender, chat_id, message_id, upload_client=None, edit_id=None, topic=False, topic_id=None):
    """client = reader, upload_client = sender (bot). Fast file_id path first.

    topic=True fetches by topic-local message id (public/private forum topics)."""
    if upload_client is None:
        upload_client = client
    target_chat_id = user_chat_ids.get(sender, sender)
    edit = None
    if edit_id is not None:
        try:
            edit = await upload_client.get_messages(sender, edit_id)
        except Exception:
            edit = None

    try:
        if topic:
            msg = await asyncio.wait_for(
                get_topic_message(client, chat_id, topic_id, message_id),
                timeout=DOWNLOAD_TIMEOUT,
            )
        else:
            msg = await asyncio.wait_for(client.get_messages(chat_id, message_id), timeout=DOWNLOAD_TIMEOUT)

        if not msg or msg.empty:
            if edit is not None:
                await edit.edit("❌ Source message was not found or is empty.")
            return False

        caption = await _build_caption(msg, sender)
        kind = media_kind(msg)

        if kind is None:
            # Text only
            try:
                result = await upload_client.copy_message(target_chat_id, chat_id, message_id)
            except Exception:
                result = await upload_client.send_message(target_chat_id, msg.text.markdown if msg.text else "")
            return bool(result)

        needs_rename = bool(await load_delete_words(sender)) or bool(await load_replacement_words(sender)) or bool(await get_user_rename_preference(sender))
        settings = await _settings(sender)
        has_custom_thumb = bool(settings.get("thumb"))
        download_dir = tempfile.mkdtemp(prefix="devgagan_media_")
        size = msg_size(msg)
        cache_key = f"{chat_id}:{message_id}:{size}"

        result = None

        if kind in ("video", "video_doc") and (needs_rename or has_custom_thumb):
            # Rename/thumb needed → download + re-upload
            ok, result = await _download_upload_media(
                client, upload_client, sender, target_chat_id, msg, kind, caption,
                edit or None, download_dir, cache_key,
            )
            shutil.rmtree(download_dir, ignore_errors=True)
            return ok

        # ⚡ FAST PATH — zero download
        source_id = media_file_id(msg)
        if source_id:
            try:
                result = await send_by_kind(upload_client, target_chat_id, kind, source_id, caption)
                if result and client is upload_client:
                    await file_id_cache.set(cache_key, {"kind": kind, "file_id": source_id})
            except Exception:
                result = None

        if result is None:
            # Fallback for copy-protected content: download + upload
            ok, result = await _download_upload_media(
                client, upload_client, sender, target_chat_id, msg, kind, caption,
                edit or None, download_dir, cache_key,
            )
            shutil.rmtree(download_dir, ignore_errors=True)
            if not ok:
                return False

        if msg.pinned_message and result:
            await _pin_safely(result)
        await _copy_to_log_safely(result)
        shutil.rmtree(download_dir, ignore_errors=True)
        return bool(result)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        if edit is not None:
            try:
                await edit.edit(f"❌ Failed to save content.\n\nError: `{type(e).__name__}: {e}`")
            except Exception:
                pass
        return False


# ── Restricted / private link path (userbot) ─────────────────────────────────

async def _safe_edit(edit, text):
    if edit is None:
        return
    try:
        await edit.edit(text)
    except Exception:
        pass


async def _safe_delete(edit):
    if edit is None:
        return
    try:
        await edit.delete()
    except Exception:
        pass


async def get_msg(userbot, sender, edit_id, msg_link, i, message, keep_status=False):
    msg_link = normalize_link(msg_link)
    try:
        saved_settings = await asyncio.wait_for(settings_db.cached_get_data(sender), timeout=10) or {}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        try:
            await app.edit_message_text(sender, edit_id, f"❌ Could not load settings: {type(e).__name__}: {e}")
        except Exception:
            pass
        return False

    if saved_settings.get("chat_id") is not None:
        user_chat_ids[sender] = saved_settings["chat_id"]
    else:
        user_chat_ids.pop(sender, None)
    user_caption_preferences[str(sender)] = saved_settings.get("caption") or ""
    user_rename_preferences[str(sender)] = saved_settings.get("rename_tag") or ""

    if "?single" in msg_link:
        msg_link = msg_link.split("?single")[0]
    msg_id = int(msg_link.split("/")[-1]) + int(i)
    target_chat_id = user_chat_ids.get(sender, sender)

    try:
        edit = await app.get_messages(sender, edit_id)
    except Exception:
        edit = None

    if userbot is None:
        # ⚡ Public link — bot can read it directly (zero download fast path)
        parts = msg_link.split("/")
        if len(parts) >= 6 and parts[-2].isdigit():
            # public topic group: t.me/username/TOPIC/msg_id
            chat = parts[-3]
            return await copy_message_with_chat_id(
                app, sender, chat, msg_id, edit_id=edit_id,
                topic=True, topic_id=int(parts[-2]),
            )
        chat = parts[-2] if len(parts) >= 2 else msg_link
        return await copy_message_with_chat_id(app, sender, chat, msg_id, edit_id=edit_id)

    try:
        parts = msg_link.split("/")
        topic_part = None
        if 't.me/b/' in msg_link:
            # bot link: t.me/b/bot_username/msg_id
            chat = parts[-2]
            msg = await asyncio.wait_for(userbot.get_messages(chat, msg_id), timeout=DOWNLOAD_TIMEOUT)
        elif 't.me/c/' in msg_link:
            # private channel/group
            chat = int('-100' + str(parts[parts.index('c') + 1]))
            if len(parts) >= 7 and parts[-2].isdigit():
                # private topic: t.me/c/xxx/TOPIC/msg_id → topic-local id
                topic_part = int(parts[-2])
                msg = await asyncio.wait_for(
                    get_topic_message(userbot, chat, topic_part, msg_id),
                    timeout=DOWNLOAD_TIMEOUT,
                )
            else:
                msg = await asyncio.wait_for(userbot.get_messages(chat, msg_id), timeout=DOWNLOAD_TIMEOUT)
        elif len(parts) >= 6 and parts[-2].isdigit():
            # public topic group: t.me/username/TOPIC/msg_id → topic-local id
            chat = parts[-3]
            topic_part = int(parts[-2])
            msg = await asyncio.wait_for(
                get_topic_message(userbot, chat, topic_part, msg_id),
                timeout=DOWNLOAD_TIMEOUT,
            )
        else:
            # public channel/group via userbot (copy-protected)
            chat = parts[-2] if len(parts) >= 2 else msg_link
            msg = await asyncio.wait_for(userbot.get_messages(chat, msg_id), timeout=DOWNLOAD_TIMEOUT)

        if not msg:
            await _safe_edit(edit, "❌ Source message was not found or is not accessible to your logged-in account.")
            return False
        if msg.service:
            await _safe_edit(edit, "❌ This is a Telegram service message and cannot be copied.")
            return False
        if msg.empty:
            await _safe_edit(edit, "❌ Source message is empty or unavailable to your logged-in account.")
            return False

        kind = media_kind(msg)

        if kind is None:
            # Web page or plain text — send directly, no download at all
            await _safe_edit(edit, "⚡ Cloning...")
            devgaganin = await app.send_message(target_chat_id, msg.text.markdown if msg.text else "")
            if msg.pinned_message:
                await _pin_safely(devgaganin)
            await _copy_to_log_safely(devgaganin)
            if not keep_status:
                await _safe_delete(edit)
            return True

        caption = await _build_caption(msg, sender)
        size = msg_size(msg)
        cache_key = f"{chat}:{topic_part or 0}:{msg_id}:{size}"

        # ⚡ CACHE HIT — instant resend, no download/upload
        cached = await file_id_cache.get(cache_key)
        if cached and cached.get("kind") == kind:
            try:
                await _safe_edit(edit, "⚡ Sending instantly from cache...")
                result = await send_by_kind(app, target_chat_id, kind, cached["file_id"], caption)
                if result:
                    await _copy_to_log_safely(result)
                    if not keep_status:
                        await _safe_delete(edit)
                    return True
            except Exception:
                pass

        download_dir = tempfile.mkdtemp(prefix="devgagan_media_")
        ok, result = await _download_upload_media(
            userbot, app, sender, target_chat_id, msg, kind, caption,
            edit, download_dir, cache_key,
        )
        shutil.rmtree(download_dir, ignore_errors=True)

        if ok and not keep_status:
            await _safe_delete(edit)
        return ok

    except asyncio.CancelledError:
        raise
    except (ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid):
        await _safe_edit(edit, "❌ Have you joined the channel? If yes, try /login again.")
        return False
    except FloodWait as fw:
        await _safe_edit(edit, f"⚠️ Flood limit. Try again after `{fw.x}` seconds.")
        return False
    except Exception as e:
        await _safe_edit(edit, f"❌ Failed to save: `{msg_link}`\n\nError: `{type(e).__name__}: {e}`")
        return False
