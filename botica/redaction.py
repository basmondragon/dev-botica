"""Redact invitation tokens and line-forging control characters from log records.

The invitation token is the credential the whole anonymous accept flow rests on,
and a path segment is written into every proxy and application access log by
construction. The link Botica issues puts the token in a fragment, which never
reaches the wire -- and this filter is the second of the two defences, so that a
link already sitting in somebody's inbox cannot write itself down on arrival.
"""

import logging
import re

REDACTED = "[redacted]"

_SECRETS = [
    # The retired path shape, which a proxy would write into its access line.
    re.compile(r"(/accept(?:/|%2f))[^\s/?#\"']+", re.IGNORECASE),
    # A query parameter, which the same access line would carry.
    re.compile(r"([?&]token=)[^\s&#\"']+", re.IGNORECASE),
    # And a token named as a value anywhere else -- a job payload the queue
    # logs, a repr, a traceback. The plaintext exists in the email and in the
    # job's own row; it has no business in a log stream that is retained.
    re.compile(r"(\btoken['\"]?\s*[:=]\s*['\"]?)[^\s,'\"&)}\]]+", re.IGNORECASE),
]

_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

_ESCAPES = {"\n": "\\n", "\r": "\\r", "\x1b": "\\e"}


def _escape_control(match: "re.Match[str]") -> str:
    character = match.group(0)
    return _ESCAPES.get(character, f"\\x{ord(character):02x}")


def flatten(value: str) -> str:
    """Render control characters in `value` without changing ordinary text."""
    return _CONTROL.sub(_escape_control, value)


def scrub(value: str) -> str:
    """`value` with any invitation token replaced, and line structure made visible."""
    for pattern in _SECRETS:
        value = pattern.sub(r"\g<1>" + REDACTED, value)
    return flatten(value)


def carries_secret(value: str) -> bool:
    """Whether `value` holds something `scrub` would remove."""
    return any(pattern.search(value) for pattern in _SECRETS)


class RedactingFilter(logging.Filter):
    """Redact invitation tokens and forged line breaks before handler formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            return True
        scrubbed = scrub(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = None
        return True
