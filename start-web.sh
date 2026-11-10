#!/bin/sh
set -eu

cd -P "$(dirname "$0")"
port="${1:-8000}"
login_config="$HOME/.config/package-turbo-web/login.env"
if [ -f "$login_config" ] && [ -z "${PACKAGE_TURBO_LOGIN_USERNAME:-}" ] && [ -z "${PACKAGE_TURBO_LOGIN_PASSWORD_HASH:-}" ]; then
    . "$login_config"
fi

uv sync --locked --extra web
npm ci --prefix web
npm run build --prefix web

export PACKAGE_TURBO_STATIC_DIR="$PWD/web/dist/package-turbo-web/browser"
exec uv run --locked --extra web python -m uvicorn ph_web.app:app --host 127.0.0.1 --port "$port"
