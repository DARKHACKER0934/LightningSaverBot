# devggn — lightning speed engine
# 1) Parallel ranged downloads (upload.GetFile) — 8 workers × 8MB blocks
# 2) Multi-connection uploads — N TCP sessions to the same DC, 1 worker each
#    (Pyrogram's TCP transport serializes sends with a global lock, so the
#    ONLY way to upload faster is more connections)
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
from pyrogram.errors import FloodWait
from pyrogram.session import Session
from pyrogram.file_id import FileId

try:
    from hashlib import md5
except ImportError:
    from hashlib import new as _new

    def md5():
        return _new("md5")

DL_WORKERS = int(os.getenv("DL_WORKERS", "10"))
DL_BLOCK = 8 * 1024 * 1024      # bytes per worker request range
PARALLEL_MIN_SIZE = 12 * 1024 * 1024  # use parallel engine only for bigger files

UPLOAD_CONNECTIONS = int(os.getenv("UPLOAD_CONNECTIONS", "8"))
UPLOAD_PART = 512 * 1024        # Telegram's max safe part size
BIG_FILE = 10 * 1024 * 1024

_patched = False


def patch_upload_speed():
    """Replace Client.save_file with a multi-connection uploader."""
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

            is_big = file_size > BIG_FILE
            is_missing_part = file_id is not None
            file_id = file_id or self.rnd_id()
            dc_id = await self.storage.dc_id()

            if not is_big:
                # ── Small file: single connection (md5 required) ────────────
                md5_sum = md5() if not is_missing_part else None
                session = self.media_sessions.get(dc_id)
                if not session:
                    session = self.media_sessions[dc_id] = Session(
                        self, dc_id, await self.storage.auth_key(),
                        await self.storage.test_mode(), is_media=True
                    )
                    await session.start()

                part_size = UPLOAD_PART
                file_total_parts = int(math.ceil(file_size / part_size))
                try:
                    fp.seek(part_size * file_part)
                    while True:
                        chunk = fp.read(part_size)
                        if not chunk:
                            if not is_missing_part:
                                md5_sum = "".join([hex(i)[2:].zfill(2) for i in md5_sum.digest()])
                            break
                        rpc = raw.functions.upload.SaveFilePart(
                            file_id=file_id,
                            file_part=file_part,
                            bytes=chunk,
                        )
                        await session.invoke(rpc)
                        if is_missing_part:
                            return
                        if not is_missing_part:
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
                    return raw.types.InputFile(
                        id=file_id,
                        parts=file_total_parts,
                        name=file_name,
                        md5_checksum=md5_sum,
                    )
                finally:
                    if isinstance(path, (str, PurePath)):
                        fp.close()

            # ── Big file: MULTI-CONNECTION parallel upload ─────────────────
            if is_missing_part:
                # retry mode: upload just the missing part on the shared session
                session = self.media_sessions.get(dc_id)
                if not session:
                    session = self.media_sessions[dc_id] = Session(
                        self, dc_id, await self.storage.auth_key(),
                        await self.storage.test_mode(), is_media=True
                    )
                    await session.start()
                part_size = UPLOAD_PART
                file_total_parts = int(math.ceil(file_size / part_size))
                fp.seek(part_size * file_part)
                chunk = fp.read(part_size)
                rpc = raw.functions.upload.SaveBigFilePart(
                    file_id=file_id,
                    file_part=file_part,
                    file_total_parts=file_total_parts,
                    bytes=chunk,
                )
                await session.invoke(rpc)
                return raw.types.InputFileBig(
                    id=file_id, parts=file_total_parts, name=file_name
                )

            part_size = UPLOAD_PART
            file_total_parts = int(math.ceil(file_size / part_size))
            connections = max(1, min(UPLOAD_CONNECTIONS, file_total_parts))

            sessions = []
            try:
                for _ in range(connections):
                    s = Session(
                        self, dc_id, await self.storage.auth_key(),
                        await self.storage.test_mode(), is_media=True
                    )
                    await s.start()
                    sessions.append(s)
            except Exception:
                for s in sessions:
                    try:
                        await s.stop()
                    except Exception:
                        pass
                sessions = []
                session = self.media_sessions.get(dc_id)
                if not session:
                    session = self.media_sessions[dc_id] = Session(
                        self, dc_id, await self.storage.auth_key(),
                        await self.storage.test_mode(), is_media=True
                    )
                    await session.start()
                sessions = [session]
                connections = 1

            done = 0
            next_part = 0
            counter_lock = asyncio.Lock()
            done_lock = asyncio.Lock()
            fds = [open(path, "rb") for _ in range(connections)]

            async def worker(session, fdr):
                nonlocal done, next_part
                while True:
                    async with counter_lock:
                        part = next_part
                        next_part += 1
                    if part >= file_total_parts:
                        return
                    fdr.seek(part * part_size)
                    chunk = fdr.read(part_size)
                    if not chunk:
                        return
                    rpc = raw.functions.upload.SaveBigFilePart(
                        file_id=file_id,
                        file_part=part,
                        file_total_parts=file_total_parts,
                        bytes=chunk,
                    )
                    for attempt in range(5):
                        try:
                            await session.invoke(rpc)
                            break
                        except FloodWait as fw:
                            await asyncio.sleep(min(fw.x, 30) + 1)
                        except OSError:
                            await asyncio.sleep(1)
                    async with done_lock:
                        done += len(chunk)
                        current = min(done, file_size)
                    if progress:
                        func = functools.partial(
                            progress, current, file_size, *progress_args
                        )
                        if inspect.iscoroutinefunction(progress):
                            await func()
                        else:
                            await self.loop.run_in_executor(self.executor, func)

            try:
                await asyncio.gather(
                    *(worker(sessions[i], fds[i]) for i in range(connections))
                )
            finally:
                for fdr in fds:
                    try:
                        fdr.close()
                    except Exception:
                        pass
                for s in sessions:
                    if s not in self.media_sessions.values():
                        try:
                            await s.stop()
                        except Exception:
                            pass
                if isinstance(path, (str, PurePath)):
                    try:
                        fp.close()
                    except Exception:
                        pass

            return raw.types.InputFileBig(
                id=file_id, parts=file_total_parts, name=file_name
            )

    Client.save_file = fast_save_file


# ── 2) Faster download: parallel ranged GetFile ───────────────────────────────

def _media_of(message):
    for kind in ("audio", "document", "photo", "sticker", "animation", "video", "voice", "video_note"):
        m = getattr(message, kind, None)
        if m is not None:
            return m
    return None


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
