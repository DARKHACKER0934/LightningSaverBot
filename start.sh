#!/bin/sh
# start the bot cleanly, writing PID files for stop.sh
BOT_DIR=/root/Testorn
cd "$BOT_DIR"
rm -f ":LightningBot:.session"
nohup ./run.sh > "$BOT_DIR/bot.log" 2>&1 < /dev/null &
RUNSH_PID=$!
echo "$RUNSH_PID" > "$BOT_DIR/runsh.pid"
sleep 4
BOT_PID=$(pgrep -f "venv/bin/python -m devgag[a]n" | head -1)
if [ -n "$BOT_PID" ]; then
    echo "$BOT_PID" > "$BOT_DIR/bot.pid"
    echo "bot started, pid: $BOT_PID"
else
    echo "bot process not found yet, check bot.log"
fi
