#!/usr/bin/env bash
set -euo pipefail

export ODBCSYSINI="${ODBCSYSINI:-$HOME/.odbc}"

bash setup_odbc.sh
mkdir -p ui/tmp

# ODBC configuration for Microsoft ODBC Driver 17 for SQL Server
export ODBCSYSINI=/home/runner/odbc/etc
export ODBCINSTINI=/home/runner/odbc/etc/odbcinst.ini
export ODBCINI=/home/runner/odbc/etc/odbc.ini

# stop old nginx if needed
pkill nginx || true
pkill -f "gunicorn.*ui.wsgi:app" || true

# start flask app on an internal upstream port
uv run gunicorn --workers "${WEB_CONCURRENCY:-1}" --bind 127.0.0.1:3000 ui.wsgi:app &
sleep 2

# start nginx in foreground using config
exec nginx -p "$PWD" -c ui/nginx.conf -g 'daemon off;'
