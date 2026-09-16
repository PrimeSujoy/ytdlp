# NexTube Bot Deployment Guide

# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot

This guide covers Docker, Koyeb, Northflank, Railway, Render, Heroku,
Hugging Face, Streamlit, VPS/RDP, AWS, and Google Cloud.

## Before deploying

Add these required secrets in the platform dashboard, or copy `.env.example`
to `.env` on your own server:

```env
DB_URI=mongodb+srv://...
API_ID=12345678
API_HASH=your_api_hash
BOT_TOKEN=1234567890:your_bot_token
OWNER_ID=123456789
```

`ADMIN_ID`, `FORCESUB`, `DUMP_CHANNEL`, `LOG_CHANNEL`, `STRING_SESSION`, and
the proxy settings are optional. `ADMIN_ID` and `FORCESUB` accept comma- or
space-separated values.

Important operational rules:

- Keep the replica/instance count at **1**. Two polling processes with the same
  token cause Telegram `getUpdates` conflicts.
- Never commit `.env`, `secrets.toml`, bot tokens, MongoDB URIs, or string
  sessions. They are excluded by `.gitignore`.
- Container platforms should expose their `PORT`; `/health` and `/` return
  HTTP 200. Do not configure a public port for a plain VPS unless needed.
- Mount persistent storage at `/data` when the host supports volumes. MongoDB
  stores bot records, but local sessions, cookies, thumbnails, and temporary
  files use `DATA_DIR`.
- Free services may sleep, stop after quota exhaustion, or use ephemeral disks.
  A VPS or paid always-on container is the reliable choice for continuous
  downloads.

## Docker or Docker Compose

```bash
cp .env.example .env
# edit .env
docker compose up -d --build
docker compose logs -f
```

Compose mounts the `nextube_data` named volume at `/data` and restarts the one
bot container unless it is explicitly stopped. For plain Docker:

```bash
docker build -t nextube-bot .
docker run -d --name nextube-bot --restart unless-stopped \
  --env-file .env -p 7860:7860 -v nextube-data:/data nextube-bot
```

Health check: `http://SERVER_IP:7860/health`.

## Koyeb

1. Create an App and a **Web Service** from the GitHub repository.
2. Choose the repository's `Dockerfile` builder.
3. Add the required variables from `.env.example` as secrets.
4. Set `DATA_DIR=/data`. Keep the automatically supplied `PORT` value.
5. Expose one HTTP port using `PORT`, route `/`, and set health path `/health`.
6. Set scaling to exactly one instance and deploy.

A sleeping/free instance cannot guarantee 24/7 polling. A persistent volume,
if available on the selected plan, should be mounted at `/data`.

## Northflank

1. Create a **Combined Service** and connect the GitHub repository.
2. Select Dockerfile build with context `/` and file `/Dockerfile`.
3. Add required secrets under Runtime variables.
4. Add public HTTP port `7860` and set `PORT=7860`, `DATA_DIR=/data`.
5. Configure `/health` as the HTTP health check.
6. Use one instance only. Attach a volume at `/data` if the plan supports it.

## Railway

1. Create a project from the GitHub repository. Railway automatically detects
   the root `Dockerfile`.
2. Add the required environment variables and set the health-check path to
   `/health` in Service Settings.
3. Set `DATA_DIR=/data` and generate a public domain for the service.
4. Keep one replica. Mount a Railway volume at `/data` when persistence is
   required.

Railway supplies `PORT`; do not hard-code a different public port. The old
`railway.toml` config-as-code format is intentionally not used because Railway
no longer enables it for new services.

## Render

Use **New → Blueprint** and select the repository. `render.yaml` creates the
Docker web service. Enter every variable marked `sync: false`, then deploy.
Keep the instance count at one. Render's free service can sleep and its local
filesystem is ephemeral; persistent disks and reliable 24/7 execution require
an appropriate paid plan.

## Heroku Container Stack

The repository includes `heroku.yml`, `app.json`, and a web `Procfile`.

```bash
heroku create YOUR_APP_NAME --stack container
heroku config:set DB_URI='...' API_ID='...' API_HASH='...' \
  BOT_TOKEN='...' OWNER_ID='...'
git push heroku main
heroku ps:scale web=1
```

The container listens on Heroku's dynamic `PORT`. Heroku's filesystem is
ephemeral, so MongoDB remains the durable data store and local runtime files
can disappear during restarts.

## Hugging Face Docker Space

1. Create a new Space with **Docker** SDK and copy/push this repository into it.
2. In Space Settings, add the required values as **Secrets**.
3. Add `DATA_DIR=/data` as a variable. The Dockerfile exposes port `7860`.
4. Rebuild the Space and check `/health` or the Space logs.

Use one Space for a bot token. Free hardware can sleep and local storage is
ephemeral unless persistent storage is purchased.

## Streamlit Community Cloud

Choose `streamlit_app.py` as the main file. In **App settings → Secrets**, use
TOML syntax:

```toml
DB_URI = "mongodb+srv://..."
API_ID = "12345678"
API_HASH = "your_api_hash"
BOT_TOKEN = "1234567890:your_bot_token"
OWNER_ID = "123456789"
ADMIN_ID = "123456789 987654321"
FORCESUB = "@ChannelOne -1001234567890"
DUMP_CHANNEL = "0"
```

`packages.txt` installs FFmpeg, Git, and Node.js. The Streamlit entrypoint owns
the web port and starts exactly one polling child process. Community Cloud may
hibernate inactive apps and its disk is ephemeral, so this is a compatibility
option rather than dependable 24/7 hosting.

## Linux VPS, RDP, or bare metal

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
bash setup.sh
nano .env
source venv/bin/activate
python main.py
```

For boot-time startup, edit paths/user in `nextra-bot.service`, then:

```bash
sudo cp nextra-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nextra-bot
sudo journalctl -u nextra-bot -f
```

## Windows VPS or RDP

Install Python 3.11+, Git, FFmpeg, and Node.js 22+, then run PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_windows.ps1
notepad .env
.\start_bot.ps1
```

Use Windows Task Scheduler if startup on boot is required. Configure the task
to execute `start_bot.ps1` from this repository and never start a second copy.

## AWS

The Dockerfile works with ECS/Fargate, App Runner, or an EC2 instance. For
video workloads, EC2 plus Docker Compose is the simplest option because it
provides predictable CPU, disk, and long-running processes.

- Build the repository's Dockerfile or push it to ECR.
- Configure the required secrets using the service's secret manager.
- For App Runner, configure container port `7860`; for ECS, map the service to
  the container's `PORT`. Use `/health` for health checks.
- Attach persistent storage to `/data` when needed.
- Set desired task count to one.

AWS free allowances depend on account and region and should not be assumed to
provide permanent 24/7 service.

## Google Cloud

Compute Engine with Docker Compose is recommended for long-running downloads.
Cloud Run can run the container, but polling needs one warm instance and CPU
outside HTTP requests; those settings may create charges.

For Cloud Run, deploy the Dockerfile/container, add required secrets, set
`DATA_DIR=/data`, enable instance-based CPU, set minimum instances to one, and
cap maximum instances at one. Use `/health` for health monitoring. Local Cloud
Run storage is ephemeral.

## Troubleshooting

`Conflict: terminated by other getUpdates request`
: Another deployment/process is using the same bot token. Stop it and leave
  exactly one running instance.

Health check fails
: Confirm that the platform injects `PORT`, the service routes to that same
  port, and the check path is `/health`.

Bot exits immediately
: Check logs for missing/invalid `DB_URI`, `API_ID`, `API_HASH`, `BOT_TOKEN`,
  or `OWNER_ID`. MongoDB Atlas must allow the host's network access.

FFmpeg or yt-dlp errors
: Use the Dockerfile when possible. It installs FFmpeg and Node.js 22. On a
  VPS/RDP, confirm `ffmpeg -version` and `node --version` work.

Files disappear after restart
: The host uses an ephemeral filesystem. Attach a volume at `/data`, or use a
  VPS. Temporary download files are intentionally cleaned on startup.
