---
title: Lightning Saver Bot ⚡
emoji: ⚡
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
---

# ⚡ Lightning Saver Bot — Ultra-Fast Content Saver

A Pyrogram V2 Telegram bot that saves content from public/private Telegram channels and groups at **lightning speed**.

## 🚀 What's Inside (v2 — Lightning Engine)

- ⚡ **Zero-download fast path** — media is forwarded by `file_id` whenever possible (no download/upload at all)
- 💾 **File-id cache** — repeat saves are instant
- 🔥 **Userbot session pool** — logged-in sessions stay warm, so jobs start in milliseconds
- 🎬 **In-process OpenCV thumbnails** + cached thumb files (no slow ffmpeg subprocess)
- 🏃 **Turbo batch mode** — adaptive 3–5s pacing with automatic flood back-off (up to 6x faster than before)
- 📈 **1000s of users, zero lag** — O(1) DB checks, TTL caches, unified Mongo pool, no event-loop blocking
- 🎨 **Master-level UI** — emoji-rich menus, button navigation, live progress bars with speed & ETA

## Deploy on Render

1. Push this repo to GitHub.
2. Go to [dashboard.render.com](https://dashboard.render.com) → **New → Blueprint** and select the repo. (`render.yaml` sets up a Docker worker.)
3. In **Environment**, add every variable from the table below (all secrets marked 🔒).
4. Deploy. The bot starts automatically — done! ✅

> Free plan spins down when idle; use a paid instance for 24/7 running.

### Required Variables

| Name | Type | Description |
|---|---|---|
| `API_ID` | 🔒 | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | 🔒 | Telegram API hash |
| `BOT_TOKEN` | 🔒 | Token from [@BotFather](https://t.me/BotFather) |
| `MONGO_DB` | 🔒 | MongoDB connection URL |
| `OWNER_ID` | — | Owner user ID(s), separated by spaces |
| `CHANNEL_ID` | — | Force-subscribe channel ID; bot must be an admin |
| `LOG_GROUP` | — | Log group/channel ID; bot must be an admin |
| `ZAPUPI_TOKEN` | 🔒 | Payment provider token |
| `FREEMIUM_LIMIT` | — | Free-user batch limit; `0` disables |
| `PREMIUM_LIMIT` | — | Premium-user batch limit; default `5000` |
| `MAX_HEAVY_JOBS` | — | Concurrent heavy jobs; default `8` |

## Commands

- `/start` — Main menu (buttons: Batch, Settings, Login, Premium, Help)
- `/help` — Help center with FAQ buttons
- `/login` / `/logout` — Manage your userbot session
- `/batch` — Turbo bulk download between two links
- `/cancel` — Stop any ongoing process
- `/settings` — Thumbnail, caption, rename, replace/remove words, target chat
- `/pay` — Buy premium via UPI | `/myplan` — Check your plan
- `/stats`, `/add`, `/rem`, `/check`, `/gcast`, `/eval` — Owner tools

## Local Docker test

```bash
docker build -t lightning-saver .
docker run --rm -p 7860:7860 --env-file .env lightning-saver
```

Never commit `.env` or credentials. Use only content you have permission to save, per Telegram's terms.
