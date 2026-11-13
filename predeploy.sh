#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

# This is a configuration gate, not an automatic inference from a hostname.
# An operator must verify the direct connection before setting the attestation.
python scripts/check_migration_environment.py --migration-guard

limit="${MIGRATION_TIMEOUT_SECONDS:-300}"
case "$limit" in
    ''|*[!0-9]*) echo "Invalid migration wall-clock timeout" >&2; exit 78 ;;
esac
if [ "${#limit}" -gt 3 ] || [ "$limit" -lt 1 ] || [ "$limit" -gt 900 ]; then
    echo "Migration wall-clock timeout must be between 1 and 900 seconds" >&2
    exit 78
fi

# No bootstrap, fake migration or compensating downgrade is attempted.
timeout "$limit" python manage.py deploy_migrate --direct-connection-confirmed
