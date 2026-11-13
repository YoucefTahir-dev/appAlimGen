#!/usr/bin/env bash
set -o errexit

pip install --require-hashes -r requirements.lock

# Prepare optional OAuth support without enabling an export. Native tools are
# operator-provided; fail closed before Django/migrations if activation is asked
# for on a runtime that has not passed the offline tool/configuration preflight.
case "${DRIVE_BACKUP_BUILD_DEPENDENCIES:-false}" in
  [Tt][Rr][Uu][Ee]|1) pip install --only-binary=:all: --require-hashes -r requirements-drive-backup.lock ;;
  [Ff][Aa][Ll][Ss][Ee]|0) ;;
  *) echo "Invalid Drive dependency build flag" >&2; exit 1 ;;
esac
python scripts/check_drive_backup_runtime.py --expected-postgres-major 18 --build-guard

python manage.py collectstatic --noinput

# This service's paid compute plan supports a separate pre-deploy phase.
# Keep SQL migrations out of image builds. Configure `bash predeploy.sh` in
# Render before publishing this release; start.sh refuses pending migrations.
# Historical bootstrap/unknown migrations require a separate reviewed procedure.
