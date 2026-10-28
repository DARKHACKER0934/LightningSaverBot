#!/bin/sh
# Lightning Saver Bot — VPS runner with auto-restart
set -a
. /root/Testorn/.env
set +a
export PYTHONUNBUFFERED=1
cd /root/Testorn
while true; do
    /tmp/opencode/venv/bin/python -m devgagan
    echo "[$(date)] Bot exited, restarting in 5s..."
    sleep 5
done
