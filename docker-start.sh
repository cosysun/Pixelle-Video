#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo '已创建 .env，请配置账号和 COS 后重新运行；域名及证书由现有 NGINX 管理。'
  exit 1
fi
if docker compose version >/dev/null 2>&1; then
  docker compose up -d --build "$@"
else
  docker-compose up -d --build "$@"
fi
