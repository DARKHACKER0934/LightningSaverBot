---
title: Newsaverbot
emoji: 🤖
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
---

# Newsaverbot — Save Restricted Content Bot

A Pyrogram V2 Telegram bot that saves permitted content from public/private Telegram channels and groups. The Docker image also exposes a small Flask health page so Hugging Face Spaces can keep the container reachable.

## Deploy on Hugging Face Spaces

1. Create a new Space at [huggingface.co/new-space](https://huggingface.co/new-space).
2. Select **Docker** as the Space SDK.
3. Upload the project files from this repository, including `Dockerfile`, `requirements.txt`, `config.py`, the `devgagan/` folder, and `static/`.
4. In **Settings → Variables and secrets**, add every variable in the table below. Do not put secrets in source files.
5. Wait for the Docker build to complete. The Space health page is available at `/`.

### Required Variables and Secrets

| Name | Type | Description |
|---|---|---|
| `API_ID` | Secret | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | Secret | Telegram API hash |
| `BOT_TOKEN` | Secret | Token from [@BotFather](https://t.me/BotFather) |
| `MONGO_DB` | Secret | MongoDB connection URL |
| `OWNER_ID` | Variable | Telegram owner user ID(s), separated by spaces |
| `CHANNEL_ID` | Variable | Force-subscribe channel ID; the bot must be an admin |
| `LOG_GROUP` | Variable | Log group/channel ID; the bot must be an admin |
| `ZAPUPI_TOKEN` | Secret | Payment provider token used by the payment module |
| `FREEMIUM_LIMIT` | Variable | Free-user batch limit; `0` disables the limit |
| `PREMIUM_LIMIT` | Variable | Premium-user batch limit; default `5000` |

`PORT` is set automatically to `7860` by the Dockerfile. Do not commit Telegram, MongoDB, or payment credentials to GitHub or the Space repository.

## Commands

- `/start` — Start the bot
- `/login` — Login with your phone number for userbot features
- `/logout` — Clear your session
- `/batch` — Bulk download a range of links
- `/cancel` — Stop an ongoing batch process
- `/myplan` — Check premium status
- `/settings` — Configure caption, rename, and thumbnail
- `/stats`, `/add`, `/rem`, `/check`, `/gcast` — Owner-only administration commands

## Local Docker test

```bash
docker build -t newsaverbot .
docker run --rm -p 7860:7860 --env-file .env newsaverbot
```

Do not commit `.env`. Use only content that you have permission to save or redistribute and follow Telegram's terms and applicable law.
