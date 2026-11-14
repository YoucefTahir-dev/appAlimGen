"""Opt-in Linux supervisor candidate. Disabled by default; not activated.

One durable reset worker and the unchanged Gunicorn command share an instance.
No shell command construction, credential output or automatic migrations.
Either child's unexpected exit fails the unit: Render can restart it instead of
silently serving requests with an abandoned queue. Validate resources first.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def progress_token(marker):
    return marker.stat().st_mtime_ns


def web_command(env):
    def positive(name, default, maximum):
        value = int(env.get(name, default))
        if not 1 <= value <= maximum:
            raise ValueError('Invalid web process limits')
        return str(value)

    port = positive('PORT', '8000', 65535)
    workers = positive('WEB_CONCURRENCY', '2', 32)
    timeout = positive('WEB_TIMEOUT', '120', 600)
    return [sys.executable, '-m', 'gunicorn', 'gestio_stock.wsgi:application',
            '--bind', '0.0.0.0:' + port, '--workers', workers, '--timeout', timeout,
            '--access-logformat', '%(m)s %(U)s %(s)s']


class Supervisor:
    def __init__(self, *, env=None, startup_seconds=60, shutdown_seconds=30):
        self.env = dict(os.environ if env is None else env)
        self.startup_seconds = startup_seconds
        self.shutdown_seconds = shutdown_seconds
        self.children = []
        self.stopping = False

    def request_stop(self, *_):
        self.stopping = True

    def spawn(self, command):
        child = subprocess.Popen(command, env=self.env, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
        self.children.append(child)
        return child

    def stop_children(self):
        # Signal entire process groups, including Gunicorn's own workers.
        # The shared deadline is bounded, not multiplied by child count.
        for child in self.children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + self.shutdown_seconds
        for child in self.children:
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        for child in self.children:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()

    def run(self):
        # This is an explicit candidate, not an alternative default startup.
        if self.env.get('PASSWORD_RESET_COLOCATED_WORKER_ENABLED', '').lower() != 'true':
            return 2
        command = web_command(self.env)  # Validate before starting any process.
        old = {s: signal.signal(s, self.request_stop) for s in (signal.SIGTERM, signal.SIGINT)}
        folder = None
        try:
            folder = tempfile.TemporaryDirectory(prefix='reset-worker-')
            marker = Path(folder.name) / 'ready'
            wake_directory = Path(folder.name) / 'wake'
            wake_directory.mkdir(mode=0o700)
            self.env['PASSWORD_RESET_WAKE_DIRECTORY'] = str(wake_directory)
            worker = self.spawn([sys.executable, '-u', 'manage.py', 'process_password_resets',
                                 '--limit', '10', '--poll-seconds', '15', '--ready-file', str(marker)])
            deadline = time.monotonic() + self.startup_seconds
            while not marker.is_file():
                if self.stopping:
                    return 0
                if worker.poll() is not None or time.monotonic() >= deadline:
                    return 1
                time.sleep(0.1)
            if self.stopping:
                return 0
            if worker.poll() is not None:
                return 1
            previous_progress = progress_token(marker)
            last_progress = time.monotonic()
            self.spawn(command)
            while not self.stopping:
                if any(child.poll() is not None for child in self.children):
                    return 1
                now = time.monotonic()
                current_progress = progress_token(marker)
                if current_progress != previous_progress:
                    previous_progress, last_progress = current_progress, now
                elif now - last_progress > 300:
                    # Bounded recovery from a live-but-stuck worker, not
                    # proof of queue health or actual email delivery.
                    return 1
                time.sleep(0.25)
            return 0
        finally:
            try:
                self.stop_children()
            finally:
                # Keep the ready/heartbeat directory until children are reaped.
                # Otherwise a live worker could fail during graceful shutdown.
                if folder is not None:
                    folder.cleanup()
                for signum, handler in old.items():
                    signal.signal(signum, handler)


def main():
    if os.name != 'posix':
        print('Colocated reset supervisor requires a qualified Linux runtime.', file=sys.stderr)
        return 2
    try:
        return Supervisor().run()
    except Exception:
        # Process/configuration errors may embed environment or path details.
        print('Colocated reset supervisor failed; details withheld.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
