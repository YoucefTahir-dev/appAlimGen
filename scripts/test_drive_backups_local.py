"""Run Drive-backup regressions on synthetic SQLite with external access blocked.

No .env file, inherited credentials, production database, cloud service or real
subprocess is available to these tests. PostgreSQL, GPG and Drive are mocks.
This runner does NOT validate a real upload or a real PostgreSQL restoration.

From the repository root:
    .venv\\Scripts\\python.exe scripts/test_drive_backups_local.py
"""

from contextlib import ExitStack
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

SUITES = (
    "scripts.tests.test_drive_native_bridge",
    "scripts.tests.test_drive_remote_transfer",
    "scripts.tests.test_drive_synthetic_cleanup",
    "scripts.tests.test_drive_runtime_preflight",
    "scripts.tests.test_drive_oauth_launcher",
    "apps.core.tests.test_drive_backup_service",
    "apps.core.tests.test_drive_oauth",
    "apps.core.tests.test_drive_backup_views",
    "apps.core.tests.test_backups",
    "apps.accounts.tests.test_permissions",
    "apps.accounts.tests.test_rbac",
)


def forbidden_access(*_args, **_kwargs):
    raise AssertionError("External access is forbidden by the Drive-backup local test runner")


def main():
    # The only files created by the runner live in its own disposable directory.
    # Production settings are never bootstrapped before the environment and
    # dotenv guards are installed.
    with tempfile.TemporaryDirectory(prefix="elamine-drive-backup-tests-") as temporary:
        isolated_root = Path(temporary).resolve()
        environment = {
            name: os.environ[name]
            for name in ("SystemRoot", "WINDIR", "TEMP", "TMP")
            if name in os.environ
        }
        environment.update({
            "DJANGO_SETTINGS_MODULE": "gestio_stock.settings",
            "DJANGO_DEBUG": "False",
            "SECRET_KEY": "local-drive-test-only-not-a-secret-abcdefghijklmnopqrstuvwxyz-0123456789",
            "JWT_SIGNING_KEY": "local-drive-jwt-only-not-a-secret-abcdefghijklmnopqrstuvwxyz",
            "ALLOWED_HOSTS": "localhost testserver backup-test.invalid",
            "DATABASE_URL": "",
            "DATABASE_ENGINE": "sqlite",
            "DATABASE_NAME": str(isolated_root / "source.sqlite3"),
            "ALLOW_SQLITE_IN_PRODUCTION": "True",
            "MEDIA_STORAGE_BACKEND": "filesystem",
            "MEDIA_FILESYSTEM_PERSISTENT": "True",
            "MEDIA_ROOT": str(isolated_root / "media"),
            "PASSWORD_RESET_EMAIL_PROVIDER": "django",
            "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
            "AWS_EC2_METADATA_DISABLED": "true",
            "DRIVE_BACKUP_ENABLED": "false",
            "DRIVE_BACKUP_OAUTH_ENABLED": "false",
        })

        with ExitStack() as guards:
            guards.enter_context(patch.dict(os.environ, environment, clear=True))
            guards.enter_context(patch("dotenv.load_dotenv", return_value=False))
            guards.enter_context(patch.object(socket.socket, "connect", forbidden_access))
            guards.enter_context(patch.object(socket.socket, "connect_ex", forbidden_access))
            guards.enter_context(patch("socket.create_connection", forbidden_access))
            guards.enter_context(patch("socket.getaddrinfo", forbidden_access))
            guards.enter_context(patch.object(subprocess, "Popen", forbidden_access))

            import django
            from django.db.backends.base.base import BaseDatabaseWrapper

            original_ensure_connection = BaseDatabaseWrapper.ensure_connection

            def ensure_isolated_sqlite(wrapper):
                if wrapper.vendor != "sqlite":
                    raise AssertionError("Only the disposable SQLite database is permitted")
                database_name = str(wrapper.settings_dict.get("NAME", ""))
                # No libpq/service URI or SQLite URI may escape to an existing
                # database. Django's default in-memory test aliases are also
                # deliberately disabled in favour of inspectable temp files.
                if not database_name or database_name.startswith("file:") or database_name == ":memory:":
                    raise AssertionError("Only a disposable SQLite file is permitted")
                if not Path(database_name).resolve().is_relative_to(isolated_root):
                    raise AssertionError("SQLite access outside the isolated test directory is forbidden")
                return original_ensure_connection(wrapper)

            guards.enter_context(patch.object(
                BaseDatabaseWrapper, "ensure_connection", ensure_isolated_sqlite,
            ))

            django.setup()

            from django.conf import settings
            from django.db import connections
            from django.test.runner import DiscoverRunner
            from django.test.utils import override_settings

            if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
                raise RuntimeError("The test configuration did not select SQLite")
            settings.DATABASES["default"].setdefault("TEST", {}).update(
                NAME=str(isolated_root / "test.sqlite3"),
            )
            settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
            settings.SECURE_SSL_REDIRECT = False
            # A clean checkout has no collectstatic manifest. These synthetic
            # view tests exercise the templates, not a deployed asset build;
            # never borrow generated files from the developer's working tree.
            test_storages = dict(settings.STORAGES)
            test_storages["staticfiles"] = {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            }
            guards.enter_context(override_settings(STORAGES=test_storages))
            runner = DiscoverRunner(verbosity=2, interactive=False, parallel=0, keepdb=False)
            try:
                failures = runner.run_tests(SUITES)
            finally:
                connections.close_all()
            print(
                "Drive-backup tests: synthetic SQLite only; .env disabled; "
                "inherited credentials removed; sockets and real subprocesses blocked; "
                "no production/media bucket access; no real Drive upload or restore."
            )
            return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
