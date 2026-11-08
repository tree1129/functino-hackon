#!/usr/bin/env bash
set -euo pipefail

export LD_LIBRARY_PATH="/data/galbot/lib:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="/data/galbot/lib:${PYTHONPATH:-}"

cd /home/galbot/galbot-sdk-web-console
exec .venv/bin/python /home/galbot/galbot_animation/head_home.py
