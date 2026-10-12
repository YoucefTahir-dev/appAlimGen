#!/usr/bin/env bash
set -o errexit

pip install --require-hashes -r requirements.lock
python manage.py collectstatic --noinput

# Render's free plan does not provide a pre-deploy command. Run migrations
# during the build so the start command can bind the HTTP port immediately.
# Before the normalized e-mail uniqueness constraint is applied for the first
# time, stop the build if historical production data is incompatible. A failed
# Render build leaves the previous successful deploy serving traffic.
if ! python manage.py showmigrations accounts | grep -q '\[X\] 0006_unique_normalized_user_email'; then
    echo "Preparing the user e-mail audit"
    timeout ${MIGRATION_TIMEOUT_SECONDS:-300} python manage.py migrate accounts 0005 --noinput
    echo "Auditing user e-mails before applying the uniqueness constraint"
    python manage.py audit_user_emails --fail-on-conflict
fi

echo "Applying database migrations"
timeout ${MIGRATION_TIMEOUT_SECONDS:-300} python manage.py migrate --noinput
