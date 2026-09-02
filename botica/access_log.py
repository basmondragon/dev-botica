"""Scrub invitation tokens from Gunicorn's access and error logs."""

from gunicorn.glogging import Logger

from botica.redaction import RedactingFilter, scrub


class RedactingLogger(Logger):
    """Gunicorn's loggers with the request target scrubbed on both of its streams."""

    def setup(self, cfg):
        """Attach the redaction filter to both Gunicorn loggers."""
        super().setup(cfg)
        for log in (self.error_log, self.access_log):
            if not any(
                isinstance(existing, RedactingFilter) for existing in log.filters
            ):
                log.addFilter(RedactingFilter())

    def atoms(self, resp, req, environ, request_time):
        """Return the access-line atoms with every string value scrubbed."""
        atoms = super().atoms(resp, req, environ, request_time)
        return {
            key: scrub(value) if isinstance(value, str) else value
            for key, value in atoms.items()
        }
