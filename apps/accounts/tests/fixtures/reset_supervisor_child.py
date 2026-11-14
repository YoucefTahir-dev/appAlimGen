"""Native supervisor test child. No Django, credentials, database or network."""
import os
from pathlib import Path
import signal
import sys
import time

role, directory, mode, readiness_file = sys.argv[1:]
root = Path(directory)


def stop(*_):
    (root / (role + '-stopped')).write_text('stopped', encoding='ascii')
    raise SystemExit(0)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
(root / (role + '-pid')).write_text(str(os.getpid()), encoding='ascii')
if role == 'worker' and mode == 'no-ready':
    raise SystemExit(1)
(root / (role + '-started')).write_text('started', encoding='ascii')
started = time.monotonic()
while True:
    if role == 'worker':
        Path(readiness_file).touch()
        if mode == 'worker-exits' and time.monotonic() - started > 0.5:
            raise SystemExit(1)
    if role == 'web' and mode == 'web-exits' and time.monotonic() - started > 0.3:
        raise SystemExit(0)
    time.sleep(0.03)
