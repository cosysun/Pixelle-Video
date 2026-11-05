@echo off
cd /d %~dp0
call npm ci --prefix frontend || exit /b 1
call npm run build --prefix frontend || exit /b 1
uv run python -m api.app
