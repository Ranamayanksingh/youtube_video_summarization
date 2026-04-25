#!/bin/bash
# Restart the Telegram bot via launchd (survives lid close, auto-restarts on crash).
# Equivalent to: python services.py restart bot
cd "$(dirname "$0")"
source .venv/bin/activate
python services.py restart bot
