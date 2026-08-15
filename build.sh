#!/usr/bin/env bash
set -o errexit

pip install --require-hashes -r requirements.lock
python manage.py collectstatic --noinput
