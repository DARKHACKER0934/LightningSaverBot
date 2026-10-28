#!/bin/sh
# stop the bot (run.sh loop + python child) via PID files
BOT_DIR=/root/Testorn

if [ -f "$BOT_DIR/bot.pid" ]; then
    kill -9 "$(cat "$BOT_DIR/bot.pid")" 2>/dev/null
    rm -f "$BOT_DIR/bot.pid"
fi
if [ -f "$BOT_DIR/runsh.pid" ]; then
    kill -9 "$(cat "$BOT_DIR/runsh.pid")" 2>/dev/null
    rm -f "$BOT_DIR/runsh.pid"
fi
sleep 1
# safety: kill any leftover by exact name lookup not matching this script
for pid in $(pgrep -f "venv/bin/python -m devgag[a]n"); do kill -9 "$pid" 2>/dev/null; done
echo "stopped"
