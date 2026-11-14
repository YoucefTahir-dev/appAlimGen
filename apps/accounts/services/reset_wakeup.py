"""Private, ephemeral wake hints for a colocated worker; SQL remains durable.

Markers contain only technical dispatch primary keys, never recipient/token.
They are created BEFORE the outer transaction commits, so a committed dispatch
cannot be left without a wake hint by a Web crash between commit and notify.
The worker tolerates hints whose transaction has not committed (or rolled back).
"""
from itertools import islice
from pathlib import Path
import re
import time

from django.conf import settings

from apps.accounts.models import PasswordResetDispatch

NAME = re.compile(r'[1-9][0-9]{0,18}\Z')


def wake_directory():
    value = getattr(settings, 'PASSWORD_RESET_WAKE_DIRECTORY', '')
    return Path(value) if value else None


def notify_reset(pk):
    directory = wake_directory()
    if directory is not None:
        if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
            raise OSError('Reset wake directory unavailable')
        (directory / str(pk)).touch(mode=0o600, exist_ok=True)


def markers_present():
    directory = wake_directory()
    return directory is not None and any(NAME.fullmatch(path.name) for path in directory.iterdir())


def prune_markers():
    directory = wake_directory()
    if directory is None:
        return
    paths = list(islice((p for p in directory.iterdir() if NAME.fullmatch(p.name)), 1000))
    states = dict(PasswordResetDispatch.objects.filter(
        pk__in=[int(path.name) for path in paths],
    ).values_list('pk', 'state')) if paths else {}
    for path in paths:
        try:
            state = states.get(int(path.name))
            # Keep a pre-commit hint while its row is invisible. Only stale
            # rolled-back/missing rows or terminal rows lose their marker.
            if state in ('sent', 'expired', 'failed') or (
                    state is None and time.time() - path.stat().st_mtime > 600):
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
