FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    wget \
    bash \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN mkdir -p /app/downloads /app/downloads/thumbs

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip wheel \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7860

# Flask health page runs beside the Telegram bot (Render worker + HF Space friendly).
CMD ["sh", "-c", "python app.py & exec python -m devgagan"]
