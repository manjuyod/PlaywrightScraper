#!/bin/bash
set -e

# stop old nginx if needed
pkill nginx || true
pkill -f "gunicorn.*ui.wsgi:app" || true

# start flask app on an internal upstream port
python3.11 -m gunicorn --workers 1 --bind 127.0.0.1:3000 ui.wsgi:app &
sleep 2

# start nginx in foreground using config
exec nginx -p "$PWD" -c ui/nginx.conf -g 'daemon off;'
