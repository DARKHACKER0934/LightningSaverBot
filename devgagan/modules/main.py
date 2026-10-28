import random
import string
import asyncio
from pyrogram import filters, Client
from devgagan import app
from config import API_ID, API_HASH, FREEMIUM_LIMIT, PREMIUM_LIMIT, OWNER_ID
from devgagan.core.get_func import get_msg
from devgagan.core.func import *
from devgagan.core.mongo import db
from pyrogram.errors import FloodWait


async def generate_random_name(length=8):
    return ''.join(random.choices(string.ascii_lowercase, k=length))


# One owner task per user. The claim is synchronous and happens before the
# first await in every handler, so two same-user handlers cannot overlap.
active_jobs = {}


def _claim_job(user_id):
    current = asyncio.current_task()
    job = active_jobs.get(user_id)
    if job and not job["task"].done() and not job["cancel_requested"]:
        return False
    active_jobs[user_id] = {
        "task": current,
        "status_id": None,
        "userbot": None,
        "cancel_requested": False,
    }
    return True


def _job_is_running(user_id):
    job = active_jobs.get(user_id)
    return bool(job and job["task"] is asyncio.current_task() and not job["task"].done() and not job["cancel_requested"])


def _set_job_status(user_id, status_id):
    job = active_jobs.get(user_id)
    if job and job["task"] is asyncio.current_task():
        job["status_id"] = status_id


async def _edit_job_status(user_id, text):
    job = active_jobs.get(user_id)
    if not job or job.get("status_id") is None:
        return
    try:
        await app.edit_message_text(user_id, job["status_id"], text)
    except Exception:
        pass


async def _stop_userbot(userbot):
    if userbot is not None and userbot.is_connected:
        try:
            await userbot.stop()
        except Exception:
            pass


@app.on_message(filters.regex(r'https?://[^\s]+') & filters.private)
async def single_link(_, message):
    user_id = message.chat.id
    if not _claim_job(user_id):
        await message.reply("You already have an ongoing process. Please wait for it to finish or cancel it with /cancel.")
        return

    userbot = None
    link = ""
    try:
        freecheck = await chk_user(message, user_id)
        if freecheck == 1 and FREEMIUM_LIMIT == 0 and user_id not in OWNER_ID:
            await message.reply("Freemium service is currently not available. Upgrade to premium for access.")
            return

        join = await subscribe(_, message)
        if join == 1:
            return

        raw_link = get_link(message.text)
        link = normalize_link(raw_link)
        msg = await message.reply("Processing...")
        _set_job_status(user_id, msg.id)

        link_parts = link.split("/") if link else []
        is_public_topic = (
            't.me/' in link and 't.me/c/' not in link and 't.me/+' not in link and
            't.me/b/' not in link and len(link_parts) >= 6 and link_parts[-2].isdigit()
        )

        if 't.me/' in link and 't.me/+' not in link and 't.me/c/' not in link and 't.me/b/' not in link and not is_public_topic:
            await get_msg(None, user_id, msg.id, link, 0, message)
            return

        try:
            data = await asyncio.wait_for(db.get_data(user_id), timeout=10)
        except asyncio.TimeoutError:
            await _edit_job_status(user_id, "❌ Mongo timed out while loading your login. Please try again.")
            return
        if not data or not data.get("session"):
            await _edit_job_status(user_id, "❌ Login in bot first /login karo pehle...")
            return

        try:
            userbot = Client(
                await generate_random_name(), api_id=API_ID, api_hash=API_HASH,
                device_model='pyrogram', session_string=data["session"]
            )
            active_jobs[user_id]["userbot"] = userbot
            await asyncio.wait_for(userbot.start(), timeout=30)
        except asyncio.TimeoutError:
            await _edit_job_status(user_id, "❌ Userbot startup timed out. Please try /login again.")
            return
        except Exception:
            await _edit_job_status(user_id, "❌ Login expired /login again...")
            return

        try:
            if 't.me/+' in link:
                await msg.edit_text(await userbot_join(userbot, link))
            elif 't.me/c/' in link or is_public_topic:
                await get_msg(userbot, user_id, msg.id, link, 0, message)
            else:
                await msg.edit_text("Invalid link format.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _edit_job_status(user_id, f"❌ Link error: {exc}")
    except asyncio.CancelledError:
        await _edit_job_status(user_id, "❌ Processing cancelled.")
        raise
    except FloodWait as fw:
        await _edit_job_status(user_id, f"Try again after {fw.x} seconds due to floodwait from Telegram.")
    except Exception as exc:
        await _edit_job_status(user_id, f"❌ Link error: {exc}")
    finally:
        await _stop_userbot(userbot)
        if active_jobs.get(user_id, {}).get("task") is asyncio.current_task():
            active_jobs.pop(user_id, None)


@app.on_message(filters.command("cancel") & filters.private)
async def stop_batch(_, message):
    user_id = message.chat.id
    try:
        from devgagan.modules.settings import _user_state
        if _user_state.pop(user_id, None):
            await message.reply("Settings input cancelled. Use /settings to reopen the panel.")
            return
    except ImportError:
        pass

    job = active_jobs.get(user_id)
    task = job.get("task") if job else None
    if not job or not task or task.done() or job["cancel_requested"]:
        await app.send_message(user_id, "No active processing is running to cancel.")
        return

    job["cancel_requested"] = True
    await _edit_job_status(user_id, "⏹ Cancelling current work...")
    # A handler must never cancel or await itself.
    if task is not asyncio.current_task():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    await app.send_message(user_id, "Processing cancelled. You can start a new task now.")


@app.on_message(filters.command("batch") & filters.private)
async def batch_link(_, message):
    user_id = message.chat.id
    if not _claim_job(user_id):
        await app.send_message(user_id, "You already have a batch process running. Please wait for it to complete before starting a new one.")
        return

    userbot = None
    failed_links = False
    try:
        lol = await chk_user(message, user_id)
        max_batch_size = FREEMIUM_LIMIT if lol == 1 else PREMIUM_LIMIT
        start = await app.ask(user_id, text="Please send the start link.")
        start_id = normalize_link(start.text.strip())
        last = await app.ask(user_id, text="Please send the end link.")
        last_id = normalize_link(last.text.strip())

        try:
            cs = int(start_id.split("/")[-1])
            cl = int(last_id.split("/")[-1])
        except (TypeError, ValueError):
            await app.send_message(user_id, "Invalid links. Make sure links end with a message number.")
            return
        if user_id not in OWNER_ID and (cl - cs) > max_batch_size:
            await app.send_message(user_id, f"Batch size exceeds the limit of {max_batch_size}. Upgrade to premium for larger batch sizes.")
            return

        start_parts = start_id.split("/")
        is_batch_public_topic = (
            't.me/' in start_id and 't.me/c/' not in start_id and 't.me/+' not in start_id and
            't.me/b/' not in start_id and len(start_parts) >= 6 and start_parts[-2].isdigit()
        )
        status = await app.send_message(user_id, "Starting batch processing...")
        _set_job_status(user_id, status.id)

        for i in range(cs, cl + 1):
            if not _job_is_running(user_id):
                return
            msg = None
            try:
                result = '/'.join(start_id.split('/')[:-1])
                url = f"{result}/{i}"
                link = normalize_link(get_link(url) or url)
                is_public_link = ('t.me/' in link and 't.me/b/' not in link and
                                  't.me/c/' not in link and not is_batch_public_topic)
                if is_public_link:
                    msg = await app.send_message(user_id, f"Processing link {url}...")
                    _set_job_status(user_id, msg.id)
                    if not await get_msg(None, user_id, msg.id, link, 0, message):
                        failed_links = True
                        continue
                    sleep_msg = await app.send_message(user_id, "Sleeping for 15 seconds to avoid flood...")
                    await asyncio.sleep(20)
                    await sleep_msg.delete()
                elif not any(prefix in start_id for prefix in ('t.me/c/', 't.me/b/')) and not is_batch_public_topic:
                    raise RuntimeError("unsupported link format")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failed_links = True
                if msg is not None:
                    try:
                        await msg.edit_text(f"❌ Link {i} failed: {exc}")
                    except Exception:
                        pass

        needs_userbot = any(prefix in start_id for prefix in ('t.me/c/', 't.me/b/')) or is_batch_public_topic
        if needs_userbot:
            try:
                data = await asyncio.wait_for(db.get_data(user_id), timeout=10)
            except asyncio.TimeoutError:
                await _edit_job_status(user_id, "❌ Mongo timed out while loading your login.")
                return
            if not data or not data.get("session"):
                await _edit_job_status(user_id, "❌ Login in bot first /login karo pehle...")
                return
            try:
                userbot = Client(
                    await generate_random_name(), api_id=API_ID, api_hash=API_HASH,
                    device_model='pyrogram', session_string=data["session"]
                )
                active_jobs[user_id]["userbot"] = userbot
                await asyncio.wait_for(userbot.start(), timeout=30)
            except asyncio.TimeoutError:
                await _edit_job_status(user_id, "❌ Userbot startup timed out. Please try /login again.")
                return
            except Exception:
                await _edit_job_status(user_id, "❌ Userbot startup failed. Please try /login again.")
                return

            for i in range(cs, cl + 1):
                if not _job_is_running(user_id):
                    return
                msg = None
                try:
                    result = '/'.join(start_id.split('/')[:-1])
                    url = f"{result}/{i}"
                    link = normalize_link(get_link(url) or url)
                    if 't.me/b/' not in link and 't.me/c/' not in link and not is_batch_public_topic:
                        raise RuntimeError("unsupported link format")
                    msg = await app.send_message(user_id, f"Processing link {url}...")
                    _set_job_status(user_id, msg.id)
                    if not await get_msg(userbot, user_id, msg.id, link, 0, message):
                        failed_links = True
                        continue
                    sleep_msg = await app.send_message(user_id, "Sleeping for 15 seconds to avoid flood...")
                    await asyncio.sleep(20)
                    await sleep_msg.delete()
                    await asyncio.sleep(2)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed_links = True
                    if msg is not None:
                        try:
                            await msg.edit_text(f"❌ Link {i} failed: {exc}")
                        except Exception:
                            pass

        if _job_is_running(user_id):
            if failed_links:
                await _edit_job_status(user_id, "❌ Batch finished with one or more failed links.")
            else:
                await app.send_message(user_id, "Batch completed successfully! 🎉")
    except asyncio.CancelledError:
        await _edit_job_status(user_id, "❌ Batch processing cancelled.")
        raise
    except FloodWait as fw:
        await _edit_job_status(user_id, f"Try again after {fw.x} seconds due to floodwait from Telegram.")
    except Exception as exc:
        await _edit_job_status(user_id, f"❌ Batch error: {exc}")
    finally:
        await _stop_userbot(userbot)
        if active_jobs.get(user_id, {}).get("task") is asyncio.current_task():
            active_jobs.pop(user_id, None)
