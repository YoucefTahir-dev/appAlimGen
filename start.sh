#!/usr/bin/env bash
set -o errexit

# Environment-only changes can restart a service without a build. Recheck the
# same offline guard; disabled installations do not need optional native tools.
python scripts/check_drive_backup_runtime.py --expected-postgres-major 18 --build-guard

# Read-only schema gate. Never fall back to running migrations at startup if
# the pre-deploy command was omitted, failed or applied an incomplete plan.
python manage.py deploy_migrate --check-applied

# Default remains the existing single Web process. Never start an unsupervised
# worker with "&": this explicit opt-in owns both process groups and readiness.
case "${PASSWORD_RESET_COLOCATED_WORKER_ENABLED:-false}" in
  [Tt][Rr][Uu][Ee]) exec python scripts/run_colocated_reset.py ;;
  [Ff][Aa][Ll][Ss][Ee]) ;;
  *) echo "Invalid colocated reset worker flag" >&2; exit 1 ;;
esac

echo "Starting Gunicorn on 0.0.0.0:${PORT:-8000}"
# %(U)s excludes query strings: OAuth codes/state must not enter access logs.
# Access logging remains disabled unless separately configured by the operator.
exec gunicorn gestio_stock.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout ${WEB_TIMEOUT:-120} --access-logformat '%(m)s %(U)s %(s)s'
