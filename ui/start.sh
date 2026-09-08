#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" == "Linux" ]]; then
    export ODBCSYSINI="${ODBCSYSINI:-$HOME/.odbc}"
    bash setup_odbc.sh
fi

mkdir -p ui/tmp

# stop old nginx if needed
pkill nginx || true
pkill -f "gunicorn.*ui.wsgi:app" || true

# start flask app on an internal upstream port
uv run gunicorn --workers "${WEB_CONCURRENCY:-1}" --bind 127.0.0.1:3000 ui.wsgi:app &
sleep 2

# start nginx in foreground using config
exec nginx -p "$PWD" -e /tmp/nginx_error.log -c ui/nginx.conf -g 'daemon off;'
