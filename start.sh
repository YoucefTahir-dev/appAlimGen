#!/usr/bin/env bash
set -o errexit

echo "Starting Gunicorn on 0.0.0.0:${PORT:-8000}"
exec gunicorn gestio_stock.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout ${WEB_TIMEOUT:-120}
