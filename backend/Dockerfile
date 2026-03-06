FROM python:3.12-slim

# System deps for Playwright/Chromium on Debian Bookworm
RUN apt-get update && apt-get install -y \
    wget gnupg curl \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libasound2t64 fonts-liberation libx11-6 libxext6 libxfixes3 \
    libgtk-3-0 libglib2.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

COPY . .

# Run bot.py (Telegram bot) + uvicorn (FastAPI) together via supervisor
RUN pip install supervisor

COPY supervisord.conf /etc/supervisord.conf

EXPOSE 8000
CMD ["supervisord", "-c", "/etc/supervisord.conf"]
