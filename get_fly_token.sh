#!/bin/sh
# wait for the user to send the fly token as a txt file to the bot, then grab it
cd /root/Testorn
set -a
. ./.env
set +a
/tmp/opencode/venv/bin/python - <<'EOF'
import asyncio, os, time
from pyrogram import Client

API_ID = int(os.environ["API_ID"]); API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]; OWNER = int(os.environ["OWNER_ID"])

async def main():
    app = Client("flytokenpoller", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    print("polling for token file from", OWNER, "...")
    seen = set()
    async for m in app.get_chat_history(OWNER, limit=10):
        if m.document:
            seen.add(m.id)
    print("already-seen docs:", sorted(seen))
    for _ in range(120):  # wait up to 10 min
        await asyncio.sleep(5)
        async for m in app.get_chat_history(OWNER, limit=5):
            if m.document and m.id not in seen:
                path = await m.download(in_memory=False, file_name="/tmp/opencode/flytoken.txt")
                text = open(path, "rb").read().decode("utf-8", "ignore").strip()
                print("GOT TOKEN FILE:", text[:60], "...")
                open("/tmp/opencode/flytoken_clean.txt", "w").write(text)
                print("saved to /tmp/opencode/flytoken_clean.txt")
                await app.stop()
                return
    print("timeout — no file received")
    await app.stop()

asyncio.run(main())
EOF
