"""Discover pure supervisor tests in Django CI, plus Linux native lifecycle.

Native cases are skipped on Windows, never represented as executed there.
Child environments contain only fixed synthetic values, no application config.
"""
import os
from pathlib import Path
import signal
import sys
import tempfile
from threading import Thread
import time
import unittest
from unittest.mock import patch

from scripts.run_colocated_reset import Supervisor
from scripts.tests.test_colocated_reset import ColocatedResetSupervisorTests  # noqa: F401


@unittest.skipUnless(os.name == 'posix', 'Requires native POSIX process groups and signals.')
class ColocatedResetNativeTests(unittest.TestCase):
    def run_scenario(self, mode):
        fixture = Path(__file__).parent / 'fixtures' / 'reset_supervisor_child.py'
        with tempfile.TemporaryDirectory(prefix='synthetic-reset-supervisor-') as folder:
            root = Path(folder)

            class SyntheticSupervisor(Supervisor):
                def spawn(self, command):
                    role = 'worker' if 'process_password_resets' in command else 'web'
                    marker = command[command.index('--ready-file') + 1] if role == 'worker' else str(root / 'unused')
                    return super().spawn([sys.executable, '-I', '-B', str(fixture), role, folder, mode, marker])

            # Do not inherit CI .env/configuration/secrets in native children.
            runtime = SyntheticSupervisor(env={'PASSWORD_RESET_COLOCATED_WORKER_ENABLED': 'true',
                                               'PATH': os.defpath, 'HOME': folder, 'TMPDIR': folder},
                                          startup_seconds=3, shutdown_seconds=3)
            original_term = signal.getsignal(signal.SIGTERM)
            stopper = None
            if mode == 'signal':
                def request_stop_after_web_ready():
                    deadline = time.monotonic() + 5
                    while not (root / 'web-started').is_file():
                        if time.monotonic() >= deadline:
                            runtime.request_stop()
                            return
                        time.sleep(0.01)
                    # run() has installed its handler before spawning children.
                    os.kill(os.getpid(), signal.SIGTERM)
                stopper = Thread(target=request_stop_after_web_ready)
                stopper.start()
            try:
                result = runtime.run()
            finally:
                if stopper:
                    stopper.join(timeout=6)
            self.assertEqual(signal.getsignal(signal.SIGTERM), original_term)
            self.assertTrue(all(child.poll() is not None for child in runtime.children))
            self.assertTrue(all(child.returncode is not None for child in runtime.children))
            return result, {p.name for p in root.iterdir()}

    def test_native_sigterm_is_forwarded_to_both_children_and_reaped(self):
        result, files = self.run_scenario('signal')
        self.assertEqual(result, 0)
        self.assertTrue({'worker-stopped', 'web-stopped'}.issubset(files))

    def test_native_unexpected_worker_failure_stops_web(self):
        result, files = self.run_scenario('worker-exits')
        self.assertEqual(result, 1)
        self.assertIn('web-stopped', files)

    def test_native_web_exit_stops_worker_even_when_exit_code_is_zero(self):
        result, files = self.run_scenario('web-exits')
        self.assertEqual(result, 1)
        self.assertIn('worker-stopped', files)

    def test_native_missing_readiness_never_starts_web(self):
        result, files = self.run_scenario('no-ready')
        self.assertEqual(result, 1)
        self.assertNotIn('web-started', files)


class ColocatedResetStartupContractTests(unittest.TestCase):
    def test_default_start_is_unchanged_and_all_existing_gates_remain_first(self):
        root = Path(__file__).resolve().parents[3]
        start = (root / 'start.sh').read_text(encoding='utf-8')
        self.assertIn('${PASSWORD_RESET_COLOCATED_WORKER_ENABLED:-false}', start)
        self.assertLess(start.index('check_drive_backup_runtime.py'), start.index('run_colocated_reset.py'))
        self.assertLess(start.index('deploy_migrate --check-applied'), start.index('run_colocated_reset.py'))
        self.assertIn('exec gunicorn gestio_stock.wsgi:application', start)
        self.assertIn("--access-logformat '%(m)s %(U)s %(s)s'", start)
        build = (root / 'build.sh').read_text(encoding='utf-8')
        self.assertIn('${PASSWORD_RESET_COLOCATED_WORKER_ENABLED:-false}', build)
        self.assertIn('--require-hashes -r requirements-drive-backup.lock', build)
        blueprint = (root / 'render.yaml').read_text(encoding='utf-8')
        self.assertNotIn('type: worker', blueprint)
