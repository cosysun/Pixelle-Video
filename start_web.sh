#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
npm ci --prefix frontend
npm run build --prefix frontend
uv run python -m api.app
