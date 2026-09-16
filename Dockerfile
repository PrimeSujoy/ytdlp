FROM node:22-bookworm-slim AS node_runtime

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    HOME=/home/appuser \
    PORT=7860 \
    DATA_DIR=/data

# Install system utilities and FFmpeg. Node 22 is copied from the official
# runtime image below so yt-dlp EJS never gets an outdated Debian Node build.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
RUN node --version

RUN useradd --create-home --uid 1000 appuser && \
    mkdir -p /app /data/downloads /data/cookies && \
    chown -R appuser:appuser /app /data

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files. UID 1000 is compatible with Hugging Face Spaces and
# also avoids running the bot as root on other container platforms.
COPY --chown=appuser:appuser . .

EXPOSE 7860

VOLUME ["/data"]

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# Start the bot
CMD ["python", "main.py"]
