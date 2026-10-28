# devggn — lightning speed engine
# 1) Parallel ranged downloads (upload.GetFile) — 8 workers × 8MB blocks
# 2) Patched save_file upload — 1MB parts, 12 workers (Pyrogram default: 4)
# 3) Instant-speed progress reporting

import asyncio
import functools
import inspect
import math
import os
import time
from pathlib import PurePath

import pyrogram
from pyrogram import raw
from pyrogram import StopTransmission
from pyrogram.client import Client
from pyrogram.session import Session
from pyrogram.file_id import FileId

try:
    from hashlib import md5
except ImportError:
    from hashlib import new as _new

    def md5():
        return _new("md5")

DL_WORKERS = int(os.getenv("DL_WORKERS", "8"))
DL_BLOCK = 8 * 1024 * 1024      # bytes per worker request range
PARALLEL_MIN_SIZE = 12 * 1024 * 1024  # use parallel engine only for bigger files

_patched = False


# ── 1) Faster upload: override Client.save_file ───────────────────────────────

def patch_upload_speed():
    """Replace Pyrogram's save_file with a high-concurrency version."""
    global _patched
    if _patched:
        return
    _patched = True

    async def fast_save_file(
        self: "pyrogram.Client",
        path,
        file_id=None,
        file_part=0,
        progress=None,
        progress_args=(),
    ):
        async with self.save_file_semaphore:
            if path is None:
                return None

            async def worker(session):
                while True:
                    data = await queue.get()

                    if data is None:
                        return

                    try:
                        await session.invoke(data)
                    except Exception as e:
                        import logging
                        logging.getLogger("pyrogram").exception(e)

            part_size = 1024 * 1024          # 1MB parts (Pyrogram default: 512KB)

            if isinstance(path, (str, PurePath)):
                fp = open(path, "rb")
            elif isinstance(path, __import__("io").IOBase):
                fp = path
            else:
                raise ValueError("Invalid file. Expected a file path as string or a binary (not text) file pointer")

            file_name = getattr(fp, "name", "file.jpg")

            fp.seek(0, os.SEEK_END)
            file_size = fp.tell()
            fp.seek(0)

            if file_size == 0:
                raise ValueError("File size equals to 0 B")

            if self.me and getattr(self.me, "is_premium", False):
                file_size_limit_mib = 4000
            else:
                file_size_limit_mib = 2000

            if file_size > file_size_limit_mib * 1024 * 1024:
                raise ValueError(f"Can't upload files bigger than {file_size_limit_mib} MiB")

            file_total_parts = int(math.ceil(file_size / part_size))
            is_big = file_size > 10 * 1024 * 1024
            workers_count = 12 if is_big else 1
            is_missing_part = file_id is not None
            file_id = file_id or self.rnd_id()
            md5_sum = md5() if not is_big and not is_missing_part else None
            dc_id = await self.storage.dc_id()

            session = self.media_sessions.get(dc_id)
            if not session:
                session = self.media_sessions[dc_id] = Session(
                    self, dc_id, await self.storage.auth_key(),
                    await self.storage.test_mode(), is_media=True
                )
                await session.start()

            workers = [self.loop.create_task(worker(session)) for _ in range(workers_count)]
            queue = asyncio.Queue(workers_count * 2)

            try:
                fp.seek(part_size * file_part)

                while True:
                    chunk = fp.read(part_size)

                    if not chunk:
                        if not is_big and not is_missing_part:
                            md5_sum = "".join([hex(i)[2:].zfill(2) for i in md5_sum.digest()])
                        break

                    if is_big:
                        rpc = raw.functions.upload.SaveBigFilePart(
                            file_id=file_id,
                            file_part=file_part,
                            file_total_parts=file_total_parts,
                            bytes=chunk,
                        )
                    else:
                        rpc = raw.functions.upload.SaveFilePart(
                            file_id=file_id,
                            file_part=file_part,
                            bytes=chunk,
                        )

                    await queue.put(rpc)

                    if is_missing_part:
                        return

                    if not is_big and not is_missing_part:
                        md5_sum.update(chunk)

                    file_part += 1

                    if progress:
                        func = functools.partial(
                            progress,
                            min(file_part * part_size, file_size),
                            file_size,
                            *progress_args,
                        )

                        if inspect.iscoroutinefunction(progress):
                            await func()
                        else:
                            await self.loop.run_in_executor(self.executor, func)
            except StopTransmission:
                raise
            except Exception as e:
                import logging
                logging.getLogger("pyrogram").exception(e)
            else:
                if is_big:
                    return raw.types.InputFileBig(
                        id=file_id,
                        parts=file_total_parts,
                        name=file_name,
                    )
                else:
                    return raw.types.InputFile(
                        id=file_id,
                        parts=file_total_parts,
                        name=file_name,
                        md5_checksum=md5_sum,
                    )
            finally:
                for _ in workers:
                    await queue.put(None)

                await asyncio.gather(*workers)

                if isinstance(path, (str, PurePath)):
                    fp.close()

    Client.save_file = fast_save_file


# ── 2) Faster download: parallel ranged GetFile ───────────────────────────────

def _media_of(message):
    for kind in ("audio", "document", "photo", "sticker", "animation", "video", "voice", "video_note"):
        m = getattr(message, kind, None)
        if m is not None:
            return m
    return None


def _ext_for(client, media):
    mime = getattr(media, "mime_type", "") or ""
    try:
        return client.guess_extension(mime) or "bin"
    except Exception:
        return "bin"


async def fast_download(client, message, file_path, progress=None, progress_args=()):
    """Parallel ranged download. Falls back to Pyrogram's downloader if anything fails."""
    media = _media_of(message)
    if media is None:
        raise ValueError("No downloadable media found")

    file_size = getattr(media, "file_size", 0) or 0
    if file_size < PARALLEL_MIN_SIZE or not hasattr(client, "get_file"):
        return await client.download_media(
            message, file_name=file_path,
            progress=progress, progress_args=progress_args,
        )

    file_id = FileId.decode(media.file_id)
    chunk_size = 1024 * 1024
    total_chunks = int(math.ceil(file_size / chunk_size))
    block_chunks = max(1, DL_BLOCK // chunk_size)
    total_blocks = int(math.ceil(total_chunks / block_chunks))
    workers = min(DL_WORKERS, total_blocks)

    fd = open(file_path, "wb")
    fd.truncate(file_size)
    fd.close()

    done = 0
    next_block = 0
    lock = asyncio.Lock()

    async def download_block():
        nonlocal done, next_block
        while True:
            async with lock:
                block = next_block
                next_block += 1
            if block >= total_blocks:
                return
            start_chunk = block * block_chunks
            chunks = []
            async for data in client.get_file(
                file_id, file_size, offset=start_chunk, limit=block_chunks
            ):
                chunks.append(data)
            data = b"".join(chunks)
            off = start_chunk * chunk_size
            with open(file_path, "r+b") as f:
                f.seek(off)
                f.write(data)
            async with lock:
                done += len(data)
                current = min(done, file_size)
            if progress:
                func = functools.partial(progress, current, file_size, *progress_args)
                if inspect.iscoroutinefunction(progress):
                    await func()
                else:
                    await client.loop.run_in_executor(client.executor, func)

    try:
        await asyncio.gather(*(download_block() for _ in range(workers)))
        # safety: clamp to the exact declared size (server may pad the last block)
        with open(file_path, "r+b") as f:
            f.truncate(file_size)
    except StopTransmission:
        raise
    except Exception:
        # fallback: clean sequential download
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        return await client.download_media(
            message, file_name=file_path,
            progress=progress, progress_args=progress_args,
        )

    return file_path
