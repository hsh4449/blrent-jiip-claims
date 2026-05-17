#!/bin/bash
# Vultr cron 실행 진입점. 환경변수는 .env 에서 로드.
set -e
cd "$(dirname "$0")"
set -a
. ./.env
set +a
mkdir -p logs
exec ./venv/bin/python notifier.py
