# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ is required and was not found in PATH."
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "FFmpeg was not found in PATH. Install FFmpeg before downloading media."
}

python -m venv venv
& "$PSScriptRoot\venv\Scripts\python.exe" -m pip install --upgrade pip
& "$PSScriptRoot\venv\Scripts\python.exe" -m pip install -r requirements.txt

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
}
New-Item -ItemType Directory -Force -Path "$PSScriptRoot\data" | Out-Null

Write-Host "Setup complete. Edit .env, then run .\start_bot.ps1" -ForegroundColor Green
