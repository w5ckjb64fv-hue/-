#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
( sleep 1; python3 -m webbrowser http://127.0.0.1:7860 >/dev/null 2>&1 || true ) &
python3 app.py
