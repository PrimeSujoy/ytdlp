#!/bin/bash
# NexTube Bot - Quick Setup Script for Ubuntu/Debian VPS
# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot

set -e

echo "============================================"
echo "  NexTube Bot - Setup Script"
echo "============================================"
echo ""

# Update system
echo "[1/5] Updating system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg git curl python3 python3-pip python3-venv nodejs > /dev/null 2>&1

# Check Python version
echo "[2/5] Checking Python version..."
python3 --version

# Create virtual environment
echo "[3/5] Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "[4/5] Installing Python dependencies..."
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt > /dev/null 2>&1

# Create required directories
echo "[5/5] Creating required directories..."
mkdir -p data/downloads data/cookies

echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
if [ ! -f .env ] && [ -f .env.example ]; then
    cp .env.example .env
fi

echo "Next steps:"
echo "  1. Edit .env with your credentials"
echo "  2. Start the bot:  source venv/bin/activate && python main.py"
echo ""
echo "For systemd service (auto-start on boot):"
echo "  sudo cp nextra-bot.service /etc/systemd/system/"
echo "  sudo systemctl enable nextra-bot"
echo "  sudo systemctl start nextra-bot"
echo ""
