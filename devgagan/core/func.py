# devgaganin — core helpers: progress bar v2, fast metadata, fast thumbs


import asyncio
import json
import math
import os
import re
import time
import tempfile

import cv2
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import (
    FloodWait,
    InviteHashInvalid,
    InviteHashExpired,
    UserAlreadyParticipant,
    UserNotParticipant,
    ChannelInvalid,
    PeerIdInvalid,
    UsernameNotOccupied,
)
from config import CHANNEL_ID, OWNER_ID
from devgagan.core import script
from devgagan.core.cache import premium_cache


def normalize_link(link):
    """Normalize telegram.me → t.me (Telegram domain change fix)."""
    if not link:
        return link
    for old in (
        "https://www.telegram.me/",
        "https://telegram.me/",
        "http://telegram.me/",
    ):
        link = link.replace(old, "https://t.me/")
    return link


async def chk_user(message, user_id):
    """0 = premium/owner, 1 = free. Premium set is cached 60s (no DB scan)."""
    user = await premium_users()
    if user_id in user or user_id in OWNER_ID:
        return 0
    return 1


async def premium_users():
    cached = await premium_cache.get("_all_")
    if cached is not None:
        return cached
    from devgagan.core.mongo.plans_db import premium_users as _load

    try:
        ids = set(await _load())
    except Exception:
        ids = set()
    await premium_cache.set("_all_", ids)
    return ids


async def invalidate_premium_cache():
    await premium_cache.invalidate("_all_")


async def gen_link(app, chat_id):
    link = await app.export_chat_invite_link(chat_id)
    return link


_invite_cache = {}  # channel_id -> (url, expiry)


async def _get_invite_link(app, chat_id):
    """Cached invite link (avoids creating a new link on every message)."""
    cached = _invite_cache.get(chat_id)
    if cached and cached[1] > time.time():
        return cached[0]
    try:
        url = await app.export_chat_invite_link(chat_id)
    except Exception:
        return None
    _invite_cache[chat_id] = (url, time.time() + 3600)
    return url


async def subscribe(app, message):
    """Force-subscribe check. Returns 0 = allowed, 1 = blocked (join prompt sent)."""
    update_channel = CHANNEL_ID
    if not update_channel:
        return 0

    user_id = message.from_user.id

    try:
        user = await app.get_chat_member(update_channel, user_id)
        if user.status in ("kicked", "banned", "restricted"):
            await message.reply_text(
                "🚫 **You are banned from the updates channel.**\n"
                "Contact -- @rajputserver"
            )
            return 1
        return 0
    except (UserNotParticipant, PeerIdInvalid):
        # Never interacted / not a member → show join prompt
        url = await _get_invite_link(app, update_channel)
        keyboard = [[InlineKeyboardButton("🔗 Join Channel", url=url)] if url else []]
        keyboard.append([InlineKeyboardButton("✅ I've Joined", callback_data=f"verifyjoin_{user_id}")])
        try:
            await message.reply_photo(
                photo="https://i.postimg.cc/vH7NtYnn/file-00000000df88622fab8ba266a852d070.png",
                caption=script.FORCE_MSG.format(message.from_user.mention),
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            try:
                await message.reply_text(
                    script.FORCE_MSG.format(message.from_user.mention),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except Exception:
                pass
        return 1
    except (ChannelInvalid, UsernameNotOccupied):
        # Channel itself is misconfigured — never block real users because of it
        print(
            f"WARNING: CHANNEL_ID={update_channel} is invalid or inaccessible; "
            "skipping force-subscription check."
        )
        return 0
    except Exception as exc:
        # Transient error — log and allow (don't lock out everyone)
        print(f"Force-subscription check failed: {type(exc).__name__}: {exc}")
        return 0


async def get_seconds(time_string):
    def extract_value_and_unit(ts):
        value = ""
        unit = ""

        index = 0
        while index < len(ts) and ts[index].isdigit():
            value += ts[index]
            index += 1

        unit = ts[index:].lstrip()

        if value:
            value = int(value)

        return value, unit

    value, unit = extract_value_and_unit(time_string)

    if unit == "s":
        return value
    elif unit == "min":
        return value * 60
    elif unit == "hour":
        return value * 3600
    elif unit == "day":
        return value * 86400
    elif unit == "month":
        return value * 86400 * 30
    elif unit == "year":
        return value * 86400 * 365
    else:
        return 0


# ── Progress bar v2 (smooth, rich, every ~3s) ─────────────────────────────────

BAR_SEGMENTS = 12
BAR_DONE = "🟩"
BAR_TODO = "⬛"


def make_bar(percentage: float) -> str:
    filled = math.floor(percentage / 100 * BAR_SEGMENTS)
    return BAR_DONE * filled + BAR_TODO * (BAR_SEGMENTS - filled)


PROGRESS_TEMPLATE = (
    "{bar}\n\n"
    "⚡ **Speed** : `{speed}`\n"
    "📦 **Done** : `{current}` / `{total}`\n"
    "📊 **Progress** : `{percent}%`\n"
    "⏳ **ETA** : `{eta}`"
)

_last_update = {}  # id(message) -> (time, bytes)


async def progress_bar(current, total, ud_type, message, start):
    """Throttled progress editor with INSTANT speed (delta-based, not average)."""
    now = time.time()
    key = (id(message), start)
    prev = _last_update.get(key)

    if current != total and prev is not None and now - prev[0] < 2.5:
        return

    try:
        percentage = current * 100 / total
        if prev is not None and now > prev[0]:
            speed = (current - prev[1]) / (now - prev[0])
        else:
            speed = current / (now - start) if now > start else 0
        if speed < 0:
            speed = 0
        _last_update[key] = (now, current)
        eta_seconds = int((total - current) / speed) if speed > 0 else 0

        text = PROGRESS_TEMPLATE.format(
            bar=make_bar(percentage),
            speed=f"{humanbytes(speed)}/s",
            current=humanbytes(current),
            total=humanbytes(total),
            percent=round(percentage, 2),
            eta=TimeFormatter(eta_seconds * 1000) if eta_seconds else "Almost done ⚡",
        )
        try:
            await message.edit(text=f"{ud_type}\n\n{text}")
        except Exception:
            pass
        if current >= total:
            _last_update.pop(key, None)
    except Exception:
        pass


def humanbytes(size):
    if not size:
        return ""
    power = 2**10
    n = 0
    dic_power_n = {0: " ", 1: "K", 2: "M", 3: "G", 4: "T"}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + dic_power_n[n] + "B"


def TimeFormatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = (
        ((str(days) + "d ") if days else "")
        + ((str(hours) + "h ") if hours else "")
        + ((str(minutes) + "m ") if minutes else "")
        + ((str(seconds) + "s ") if seconds else "")
    )
    return tmp.strip()


def convert(seconds):
    seconds = seconds % (24 * 3600)
    hour = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    return "%d:%02d:%02d" % (hour, minutes, seconds)


async def userbot_join(userbot, invite_link):
    try:
        await userbot.join_chat(invite_link)
        return "Successfully joined the Channel"
    except UserAlreadyParticipant:
        return "User is already a participant."
    except (InviteHashInvalid, InviteHashExpired):
        return "Could not join. Maybe your link is expired or Invalid."
    except FloodWait as fw:
        return f"Too many requests, try again after {fw.x} seconds."
    except Exception as e:
        print(e)
        return "Could not join, try joining manually."


def get_link(string):
    regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
    url = re.findall(regex, string)
    try:
        link = [x[0] for x in url][0]
        if link:
            return link
        else:
            return False
    except Exception:
        return False


# ── Fast metadata via ffprobe (no frame decode) ───────────────────────────────

async def video_metadata(file):
    """Read width/height/duration from container headers (fast)."""
    default_values = {"width": 1280, "height": 720, "duration": 1}
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0 or not out:
            return await _cv2_metadata_async(file)
        info = json.loads(out.decode("utf-8", "ignore") or "{}")
        width, height, duration = 0, 0, 0.0
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video" and not width:
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
            if not duration and stream.get("duration"):
                duration = float(stream["duration"])
        if not duration:
            duration = float(info.get("format", {}).get("duration") or 0)
        if width and height and duration:
            return {"width": width, "height": height, "duration": round(duration)}
        return await _cv2_metadata_async(file)
    except Exception as e:
        print(f"ffprobe metadata failed: {e}")
        return await _cv2_metadata_async(file)


async def _cv2_metadata_async(file):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _cv2_metadata, file)


def _cv2_metadata(file):
    default_values = {"width": 1280, "height": 720, "duration": 1}
    try:
        vcap = cv2.VideoCapture(file)
        if not vcap.isOpened():
            return default_values
        width = round(vcap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = round(vcap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = vcap.get(cv2.CAP_PROP_FPS)
        frame_count = vcap.get(cv2.CAP_PROP_FRAME_COUNT)
        vcap.release()
        if fps > 0 and frame_count > 0:
            duration = round(frame_count / fps)
            if duration > 0:
                return {"width": width, "height": height, "duration": duration}
        return default_values
    except Exception:
        return default_values


def _extract_frame_cv2(video, out, seek_sec=None, width=320):
    """In-process frame extraction — much faster than an ffmpeg subprocess."""
    cap = cv2.VideoCapture(video)
    try:
        if seek_sec:
            cap.set(cv2.CAP_PROP_POS_MSEC, seek_sec * 1000)
        ret, frame = cap.read()
        if not ret or frame is None:
            return False
        h, w = frame.shape[:2]
        if w > width:
            new_h = int(h * width / w)
            frame = cv2.resize(frame, (width, new_h), interpolation=cv2.INTER_AREA)
        return cv2.imwrite(out, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    finally:
        cap.release()


def hhmmss(seconds):
    return time.strftime("%H:%M:%S", time.gmtime(seconds))


async def screenshot(video, duration, sender):
    """Generate a thumbnail frame using OpenCV (non-blocking, in-process)."""
    if os.path.exists(f"{sender}.jpg"):
        return f"{sender}.jpg"
    fd, out = tempfile.mkstemp(prefix="devgagan_thumb_", suffix=".jpg")
    os.close(fd)
    seek_sec = float(duration) / 2 if duration else None
    try:
        ok = await asyncio.get_running_loop().run_in_executor(
            None, _extract_frame_cv2, video, out, seek_sec, 320
        )
    except Exception:
        ok = False
    if ok and os.path.isfile(out) and os.path.getsize(out) > 0:
        return out
    if os.path.exists(out):
        os.remove(out)
    return None
