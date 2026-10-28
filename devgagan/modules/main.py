import asyncio
import os
import time

from pyrogram import filters
from devgagan import app
from config import FREEMIUM_LIMIT, PREMIUM_LIMIT, OWNER_ID
from devgagan.core.get_func import get_msg
from devgagan.core.func import chk_user, subscribe, normalize_link, get_link
from devgagan.core.pool import userbot_pool
from devgagan.core.mongo import db
from pyrogram.errors import FloodWait

# One owner task per user. The claim is synchronous and happens before the
# first await in every handler, so two same-user handlers cannot overlap.
active_jobs = {}

# Global concurrency guard for heavy download/upload jobs — keeps things
# butter-smooth even with 1000s of users.
MAX_HEAVY_JOBS = int(os.environ.get("MAX_HEAVY_JOBS", "8"))
heavy_semaphore = asyncio.Semaphore(MAX_HEAVY_JOBS)


def _claim_job(user_id):
    current = asyncio.current_task()
    job = active_jobs.get(user_id)
    if job and not job["task"].done() and not job["cancel_requested"]:
        return False
    active_jobs[user_id] = {
        "task": current,
        "status_id": None,
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


async def _get_userbot(user_id):
    """Acquire a warm userbot from the pool (instant after first login)."""
    try:
        data = await asyncio.wait_for(db.get_data(user_id), timeout=10)
    except asyncio.TimeoutError:
        await _edit_job_status(user_id, "❌ Mongo timed out while loading your login. Please try again.")
        return None
    if not data or not data.get("session"):
        await _edit_job_status(user_id, "❌ Login in bot first /login karo pehle...")
        return None
    try:
        return await userbot_pool.acquire(user_id, data["session"])
    except TimeoutError:
        await _edit_job_status(user_id, "❌ Userbot startup timed out. Please try /login again.")
        return None
    except Exception:
        await _edit_job_status(user_id, "❌ Login expired /login again...")
        return None


def _release_userbot(user_id):
    try:
        userbot_pool.release(user_id)
    except Exception:
        pass


def _parse_parts(link):
    parts = link.split("/") if link else []
    is_public_topic = (
        't.me/' in link and 't.me/c/' not in link and 't.me/+' not in link and
        't.me/b/' not in link and len(parts) >= 6 and parts[-2].isdigit()
    )
    return parts, is_public_topic


@app.on_message(filters.regex(r'https?://[^\s]+') & filters.private)
async def single_link(_, message):
    user_id = message.chat.id
    if not _claim_job(user_id):
        await message.reply("You already have an ongoing process. Please wait for it to finish or cancel it with /cancel.")
        return

    userbot = None
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
        msg = await message.reply("⚡ **Starting lightning save...**")
        _set_job_status(user_id, msg.id)

        _, is_public_topic = _parse_parts(link)

        if 't.me/' in link and 't.me/+' not in link and 't.me/c/' not in link and 't.me/b/' not in link and not is_public_topic:
            await get_msg(None, user_id, msg.id, link, 0, message)
            return

        async with heavy_semaphore:
            userbot = await _get_userbot(user_id)
            if userbot is None:
                return

            try:
                if 't.me/+' in link:
                    from devgagan.core.func import userbot_join
                    await msg.edit_text(await userbot_join(userbot, link))
                elif 't.me/c/' in link or is_public_topic:
                    await get_msg(userbot, user_id, msg.id, link, 0, message)
                else:
                    await msg.edit_text("❌ Invalid link format.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _edit_job_status(user_id, f"❌ Link error: {exc}")
    except asyncio.CancelledError:
        await _edit_job_status(user_id, "❌ Processing cancelled.")
        raise
    except FloodWait as fw:
        await _edit_job_status(user_id, f"⚠️ Try again after {fw.x} seconds due to floodwait from Telegram.")
    except Exception as exc:
        await _edit_job_status(user_id, f"❌ Link error: {exc}")
    finally:
        _release_userbot(user_id)
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
    if task is not asyncio.current_task():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    await app.send_message(user_id, "✅ Processing cancelled. You can start a new task now.")


class SmartPacer:
    """Adaptive 3-5s pacing; backs off automatically on FloodWait."""

    def __init__(self):
        self.interval = 3.0

    async def wait(self):
        await asyncio.sleep(self.interval)

    def on_floodwait(self, x):
        self.interval = min(30.0, max(5.0, x + 1))

    def on_success(self):
        self.interval = max(3.0, self.interval * 0.85)


def _batch_target(link):
    """Return 'public' or 'userbot' for a batch link."""
    _, is_public_topic = _parse_parts(link)
    is_public = (
        't.me/' in link and 't.me/b/' not in link and
        't.me/c/' not in link and not is_public_topic
    )
    return "public" if is_public else "userbot"


async def _batch_loop(user_id, message, userbot, start_id, cl, cs, status_id, target, start_time):
    pacer = SmartPacer()
    done = 0
    failed = 0
    total = cl - cs + 1
    prefix = '/'.join(start_id.split('/')[:-1])

    for i in range(cs, cl + 1):
        if not _job_is_running(user_id):
            return False, done, failed
        url = f"{prefix}/{i}"
        link = normalize_link(get_link(url) or url)

        try:
            await _edit_job_status(
                user_id,
                f"⚙️ **BATCH IN PROGRESS**\n\n"
                f"🔗 Processing `{i - cs + 1}`/`{total}`\n"
                f"✅ Done: `{done}` | ❌ Failed: `{failed}`\n"
                f"⏱ Elapsed: `{int(time.time() - start_time)}s`"
            )
            ok = await get_msg(userbot, user_id, status_id, link, 0, message, keep_status=True)
            if ok:
                done += 1
                pacer.on_success()
            else:
                failed += 1
        except asyncio.CancelledError:
            raise
        except FloodWait as fw:
            failed += 1
            pacer.on_floodwait(fw.x)
            await _edit_job_status(user_id, f"⚠️ Flood limit — pausing `{fw.x}s`...")
            await asyncio.sleep(min(fw.x, 120) + 1)
            continue
        except Exception:
            failed += 1

        await pacer.wait()

    return True, done, failed


@app.on_message(filters.command("batch") & filters.private)
async def batch_link(_, message):
    user_id = message.chat.id
    if not _claim_job(user_id):
        await app.send_message(user_id, "You already have a batch process running. Please wait for it to complete before starting a new one.")
        return

    userbot = None
    try:
        lol = await chk_user(message, user_id)
        max_batch_size = FREEMIUM_LIMIT if lol == 1 else PREMIUM_LIMIT
        start = await app.ask(user_id, text="📦 Please send the start link.")
        start_id = normalize_link(start.text.strip())
        last = await app.ask(user_id, text="🏁 Please send the end link.")
        last_id = normalize_link(last.text.strip())

        try:
            cs = int(start_id.split("/")[-1])
            cl = int(last_id.split("/")[-1])
        except (TypeError, ValueError):
            await app.send_message(user_id, "❌ Invalid links. Make sure links end with a message number.")
            return
        if user_id not in OWNER_ID and (cl - cs) > max_batch_size:
            await app.send_message(user_id, f"❌ Batch size exceeds the limit of `{max_batch_size}`. Upgrade to premium for larger batches.")
            return

        target = _batch_target(start_id)
        status = await app.send_message(user_id, "🚀 **Starting TURBO batch processing...**")
        _set_job_status(user_id, status.id)
        start_time = time.time()

        if target == "userbot":
            async with heavy_semaphore:
                userbot = await _get_userbot(user_id)
                if userbot is None:
                    return
                finished, done, failed = await _batch_loop(
                    user_id, message, userbot, start_id, cl, cs, status.id, start_time
                )
        else:
            finished, done, failed = await _batch_loop(
                user_id, message, None, start_id, cl, cs, status.id, start_time
            )

        elapsed = int(time.time() - start_time)
        if finished:
            emoji = "🎉" if failed == 0 else "⚠️"
            await _edit_job_status(
                user_id,
                f"{emoji} **BATCH COMPLETED!**\n\n"
                f"✅ Success: `{done}`\n"
                f"❌ Failed: `{failed}`\n"
                f"⏱ Total time: `{elapsed}s`\n\n"
                f"⚡ Powered by lightning engine"
            )
        else:
            await _edit_job_status(user_id, "⏹ Batch stopped.")
    except asyncio.CancelledError:
        await _edit_job_status(user_id, "❌ Batch processing cancelled.")
        raise
    except FloodWait as fw:
        await _edit_job_status(user_id, f"⚠️ Try again after {fw.x} seconds due to floodwait from Telegram.")
    except Exception as exc:
        await _edit_job_status(user_id, f"❌ Batch error: {exc}")
    finally:
        _release_userbot(user_id)
        if active_jobs.get(user_id, {}).get("task") is asyncio.current_task():
            active_jobs.pop(user_id, None)
