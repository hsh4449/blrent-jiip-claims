#!/bin/bash
# Vultr cron 진입점. .env 로드 후 인자로 받은 모듈 실행.
# 사용: ./run.sh crawl_only | ./run.sh auto_send | ./run.sh queue_worker
set -e
cd "$(dirname "$0")"
mkdir -p logs
set -a
. ./.env
set +a
exec ./venv/bin/python "$1.py"
