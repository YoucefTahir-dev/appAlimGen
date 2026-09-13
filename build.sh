#!/usr/bin/env bash
set -o errexit

pip install --require-hashes -r requirements.lock
python manage.py collectstatic --noinput

# Render's free plan does not provide a pre-deploy command. Run migrations
# during the build so the start command can bind the HTTP port immediately.
echo "Applying database migrations"
timeout ${MIGRATION_TIMEOUT_SECONDS:-300} python manage.py migrate --noinput
