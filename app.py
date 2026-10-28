import os
import threading
import time

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, render_template

# Keep the runtime directory available for modules that save temporary files.
os.makedirs("/app/downloads", exist_ok=True)

app = Flask(__name__)


@app.route("/")
def welcome():
    return render_template("welcome.html")


@app.route("/health")
def health():
    return "OK", 200


def auto_ping():
    """Keep the local health endpoint warm and optionally ping the public Space URL."""
    port = int(os.environ.get("PORT", "7860"))
    local_url = f"http://127.0.0.1:{port}/health"
    space_host = os.environ.get("SPACE_HOST", "").strip()
    if space_host and not space_host.startswith(("http://", "https://")):
        space_host = f"https://{space_host}"

    while True:
        time.sleep(300)
        try:
            requests.get(local_url, timeout=10)
        except requests.RequestException as exc:
            print(f"Auto-ping local health check failed: {exc}")

        if space_host:
            try:
                requests.get(f"{space_host.rstrip('/')}/health", timeout=15)
            except requests.RequestException as exc:
                print(f"Auto-ping public health check failed: {exc}")


def daily_factory_reboot():
    """Optionally restart the Space once per day when explicitly configured."""
    space_id = os.environ.get("SPACE_ID", "Sheetuzts/Testorn")
    restart_token = os.environ.get("HF_RESTART_TOKEN")
    if not restart_token:
        print("Scheduled factory reboot skipped: HF_RESTART_TOKEN is not set.")
        return

    try:
        from huggingface_hub import HfApi

        HfApi(token=restart_token).restart_space(
            repo_id=space_id,
            repo_type="space",
            factory_reboot=True,
        )
        print(f"Factory reboot triggered for {space_id}")
    except Exception as exc:
        print(f"Scheduled factory reboot failed: {exc}")


def start_daily_reboot_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(daily_factory_reboot, "cron", hour=20, minute=30)
    scheduler.start()
    print("Daily factory reboot scheduled for 8:30 PM IST")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7860"))
    threading.Thread(target=auto_ping, daemon=True, name="auto-ping").start()
    print("Auto-ping started (every 5 minutes)")
    start_daily_reboot_scheduler()
    app.run(host="0.0.0.0", port=port)
