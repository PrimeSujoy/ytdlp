---
title: NexTube Bot
emoji: 🎬
colorFrom: red
colorTo: purple
sdk: docker
app_file: Dockerfile
app_port: 7860
pinned: false
---

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/WZGram-Pyrogram-0088cc?style=for-the-badge&logo=telegram&logoColor=white" alt="WZGram">
  <img src="https://img.shields.io/badge/yt--dlp-Latest-red?style=for-the-badge&logo=youtube&logoColor=white" alt="yt-dlp">
  <img src="https://img.shields.io/badge/MongoDB-Motor-green?style=for-the-badge&logo=mongodb&logoColor=white" alt="MongoDB">
  <img src="https://img.shields.io/badge/FFmpeg-Required-orange?style=for-the-badge&logo=ffmpeg&logoColor=white" alt="FFmpeg">
</p>

<h1 align="center">NexTube - Advanced YouTube Downloader Bot</h1>

<p align="center">
  A production-ready YouTube Downloader Telegram Bot with Innertube fast-path, yt-dlp fallback, smart caching, and rich progress UI.
</p>

<p align="center">
  <a href="https://youtube.com/@NexonBots">
    <img src="https://img.shields.io/badge/YouTube-Subscribe-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="YouTube">
  </a>
  <a href="https://t.me/NexonBots">
    <img src="https://img.shields.io/badge/Telegram-Channel-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram">
  </a>
</p>

---

## Features

| Feature | Description |
|---------|-------------|
| **Innertube Fast-Path** | Direct stream resolution via YouTube's Android client (~0.15s - 800ms) |
| **yt-dlp Fallback** | Auto-fallback for age-gated, private, and cipher-protected videos |
| **Dump Channel Cache** | Every download is cached. Duplicate requests serve instantly in milliseconds |
| **Multi-Cookie Pool** | Multiple cookies with auto-rotation on HTTP 429/403 errors |
| **Playlist Pipeline** | Smart look-ahead prefetch, sequential delivery, codec/quality selection |
| **Rich Progress UI** | WZML-style download/upload dashboard with Pause/Resume/Cancel |
| **Dual Split Engine** | Streamable keyframe splitting and binary chunking for >2GB files |
| **4GB Upload** | Premium UserBot session for 4GB uploads, falls back to 2GB standard |
| **Codec Selector** | AVC / AV1 / VP9 / HEVC with MP4/MKV container switch |
| **Queue System** | Per-user queue limits with global position tracking |
| **Force Subscribe** | Multi-channel force-subscribe with real-time join/leave alerts |
| **Custom Destination** | Users can set personal upload channels via `/settings` |
| **Leaderboard** | Global download rankings with periodic reset |
| **Screenshots** | Generate video screenshot albums with MediaInfo |

---

## Requirements

| Dependency | Required | Notes |
|-----------|----------|-------|
| **Python 3.11+** | Yes | |
| **FFmpeg** | Yes | Video/audio merging and splitting |
| **Node.js 22+** | Recommended | For yt-dlp EJS challenge support |
| **MongoDB** | Yes | Free tier at [mongodb.com](https://www.mongodb.com) |
| **Telegram API** | Yes | From [my.telegram.org](https://my.telegram.org) |
| **aria2c** | No | Optional, not used by default |
| **MediaInfo** | No | Optional, for rich format info |

---

## Project Structure

```
nextube-bot/
├── config.py              # Environment-based configuration
├── main.py                # Bot client, handlers, and entry point
├── database.py            # Async MongoDB operations (motor)
├── innertube.py           # YouTube Innertube API resolver
├── ytdlp_runner.py        # yt-dlp subprocess runner
├── cookie_manager.py      # Multi-cookie pool manager
├── queue_manager.py       # Task queue and concurrency control
├── destination.py         # Custom upload destination validator
├── force_sub.py           # Force-subscribe handler
├── media_processor.py     # FFmpeg splitting and merging
├── mediainfo_helper.py    # MediaInfo and screenshots
├── thumbnail_helper.py    # Thumbnail download and processing
├── ui_builder.py          # Inline keyboard builders
├── delivery_ui.py         # Upload caption builder
├── progress.py            # Download/upload progress dashboard
├── uploader.py            # Telegram upload engine
├── requirements.txt       # Python dependencies
├── .env.example           # Safe environment-variable template
├── setup.sh               # VPS quick setup script
├── setup_windows.ps1      # Windows/RDP quick setup script
├── Dockerfile             # Docker build file
├── docker-compose.yml     # Docker Compose config
├── render.yaml            # Render Blueprint
├── heroku.yml             # Heroku Container Stack config
├── streamlit_app.py       # Streamlit Community Cloud entrypoint
├── DEPLOYMENT.md          # Complete platform-by-platform guide
└── nextra-bot.service     # systemd service file
```

---

## Configuration

Copy `.env.example` to `.env` for a VPS, RDP, or Docker installation. On a
managed host, add the same keys in that platform's **Variables/Secrets** page.
Never commit a real `.env` file or token to GitHub.

**Required values:**
- `DB_URI` - MongoDB connection string
- `API_ID` / `API_HASH` - From [my.telegram.org](https://my.telegram.org)
- `BOT_TOKEN` - From [@BotFather](https://t.me/BotFather)
- `OWNER_ID` - Your Telegram user ID

**Optional values:**
- `DUMP_CHANNEL` - Channel ID for instant caching (bot must be admin)
- `STRING_SESSION` - For 4GB uploads (Telegram Premium required)
- `FORCESUB` - Force-subscribe channels
- `YTDLP_PROXY_URL` - Proxy for yt-dlp

---

## Deployment

The repository now supports Docker, Docker Compose, Koyeb, Northflank,
Railway, Render, Heroku Container Stack, Hugging Face Docker Spaces,
Streamlit Community Cloud, Linux VPS/RDP, Windows RDP, AWS, and Google Cloud.
See **[DEPLOYMENT.md](DEPLOYMENT.md)** for exact platform-by-platform steps.

> Run exactly **one bot instance**. Telegram long polling rejects two replicas
> using the same bot token with a `Conflict: terminated by other getUpdates`
> error.

### Quick start: VPS (Ubuntu/Debian)

```bash
# Clone the repository
git clone https://github.com/yourusername/nextube-bot.git
cd nextube-bot

# Run setup script
bash setup.sh

# Edit secrets and configuration
nano .env

# Start the bot
source venv/bin/activate
python main.py
```

**Using systemd (auto-start on boot):**

```bash
# Copy service file
sudo cp nextra-bot.service /etc/systemd/system/

# Edit service file with your path
sudo nano /etc/systemd/system/nextra-bot.service
# Change WorkingDirectory to your actual path

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable nextra-bot
sudo systemctl start nextra-bot

# Check status
sudo systemctl status nextra-bot

# View logs
sudo journalctl -u nextra-bot -f
```

**Manual management:**
```bash
# Start
source venv/bin/activate && python main.py &

# Stop
pkill -f "python main.py"

# Restart
pkill -f "python main.py"; sleep 2; source venv/bin/activate && python main.py &
```

---

### Quick start: Docker Compose

```bash
# Clone and configure
git clone https://github.com/yourusername/nextube-bot.git
cd nextube-bot

cp .env.example .env
# Edit .env, then build and run
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

---

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot |
| `/help` | Show help message |
| `/settings` | Open settings menu |
| `/queue` | View your download queue |
| `/globalqueue` | View global queue |
| `/leaderboard` | Top downloaders ranking |
| `/history` | Your download history |
| `/cookies` | Manage cookie files |
| `/status` | Server, Telegram, and MongoDB health (Admin only) |
| `/speedtest` | Network speedtest (Admin only) |
| `/broadcast` | Broadcast message to all users (Admin only) |

---

## How It Works

1. **User sends YouTube link** → Bot extracts video info
2. **Cache check** → If cached in dump channel, serves instantly
3. **Innertube resolve** → Fast direct API call (~0.15s)
4. **yt-dlp fallback** → For protected/complex videos
5. **Quality selection** → Interactive keyboard with codec/size info
6. **Download** → yt-dlp with progress tracking
7. **Upload** → Telegram with retry and FloodWait handling
8. **Cache store** → Saved for instant future delivery

---

## Environment Variables

All config values can also be set as environment variables (overrides config.py):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DB_URI` | Yes | - | MongoDB connection string |
| `API_ID` | Yes | - | Telegram API ID |
| `API_HASH` | Yes | - | Telegram API hash |
| `BOT_TOKEN` | Yes | - | Bot token from BotFather |
| `OWNER_ID` | Yes | - | Bot owner's Telegram ID |
| `DUMP_CHANNEL` | No | `0` | Dump/cache channel ID |
| `ADMIN_ID` | No | - | Comma-separated admin IDs |
| `FORCESUB` | No | - | Comma-separated channel usernames |
| `STRING_SESSION` | No | - | Premium UserBot session |
| `LOG_CHANNEL` | No | - | Log channel ID |
| `YTDLP_PROXY_URL` | No | - | Proxy URL for yt-dlp |
| `YTDLP_PROXY_USERNAME` | No | - | Proxy username |
| `YTDLP_PROXY_PASSWORD` | No | - | Proxy password |
| `PORT` | No | platform value | HTTP health-check server port |
| `DATA_DIR` | No | `/data` or `./data` | Runtime sessions, cookies, and downloads |

---

## Troubleshooting

### Bot doesn't start
- Check `.env` or the host's Variables/Secrets page has correct credentials
- Ensure MongoDB URI is accessible
- Verify bot token is valid

### Telegram `getUpdates` conflict
- Stop all duplicate deployments or replicas using the same bot token
- Keep the service replica/instance count at exactly `1`

### FFmpeg not found
```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Docker (included in Dockerfile)
```

### yt-dlp errors
```bash
# Update yt-dlp
pip install -U yt-dlp

# Check Node.js (for EJS challenges)
node --version  # Should be 22+
```

### MongoDB connection errors
- Verify your IP is whitelisted in MongoDB Atlas
- Check `DB_URI` format is correct
- Ensure database user has read/write permissions

### Upload fails with "request entity too large"
- Standard Telegram limit: 2GB
- With STRING_SESSION + Premium: 4GB
- Bot auto-splits files exceeding limits

### Cookie errors
- Place cookies in `cookies/` directory
- Use Netscape cookie format
- Bot auto-rotates on HTTP 429/403

---

## Credits

# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot

---

## Support

- **Telegram:** [@NexonContactBot](https://t.me/NexonContactBot)
- **YouTube:** [@NexonBots](https://youtube.com/@NexonBots)
- **Channel:** [@NexonBots](https://t.me/NexonBots)

---

## License

This project is open source. Use responsibly.
