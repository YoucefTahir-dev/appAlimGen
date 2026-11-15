"""Gunicorn logger selected in both startup modes; no access log activation."""
from gunicorn.glogging import Logger

from .log_redaction import ResetLinkLogFilter, ResetSafeAtomsMixin


class ResetSafeLogger(ResetSafeAtomsMixin, Logger):
    def setup(self, cfg):
        super().setup(cfg)
        # Gunicorn owns handlers independently of Django's dictConfig.
        for logger in (self.access_log, self.error_log):
            logger.addFilter(ResetLinkLogFilter())
