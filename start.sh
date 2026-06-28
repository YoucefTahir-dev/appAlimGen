#!/usr/bin/env bash
set -o errexit

python manage.py migrate --noinput
gunicorn gestio_stock.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout ${WEB_TIMEOUT:-120}
