"""The invitation token appears in no access-log line (acceptance 10)."""

import logging

from botica.redaction import RedactingFilter, carries_secret, scrub


def test_a_retired_path_shape_is_scrubbed():
    line = 'GET /accept/abc123.def456.ghi789 HTTP/1.1" 200'
    assert "abc123" not in scrub(line)
    assert "[redacted]" in scrub(line)


def test_a_query_token_is_scrubbed():
    assert "secret" not in scrub("/api/invitations/preview?token=secret")


def test_a_token_named_as_a_value_anywhere_is_scrubbed():
    """The queue logs a job's arguments, and the plaintext token is one of
    them. It exists in the email and in the job's own row; it has no business
    in a log stream that is retained."""
    line = "Job send_invitation_email[1](token='abc.def.ghi', tenant_id='x')"
    assert "abc.def.ghi" not in scrub(line)
    assert "tenant_id='x'" in scrub(line)


def test_a_forged_line_break_is_made_visible():
    assert "\n" not in scrub("actor\nFAKE LOG LINE")


def test_the_filter_rewrites_the_record():
    record = logging.LogRecord(
        "botica", logging.INFO, __file__, 1, "GET /accept/tok3n", None, None
    )
    RedactingFilter().filter(record)
    assert "tok3n" not in record.getMessage()


def test_carries_secret_recognises_both_shapes():
    assert carries_secret("/accept/abc")
    assert carries_secret("?token=abc")
    assert not carries_secret("/api/users")


def test_djangos_own_request_log_goes_through_the_scrubber(settings):
    """Acceptance 10 · the token appears in **no** access-log line: not the web
    server's, not Django's, not for the HTML request that loads the accept
    screen. `django.server` carries its own handler in Django's defaults, so it
    is routed to the scrubbing one explicitly."""
    loggers = settings.LOGGING["loggers"]
    for name in ("django.server", "django.request"):
        assert loggers[name]["handlers"] == ["console"]
        assert loggers[name]["propagate"] is False
    handler = settings.LOGGING["handlers"]["console"]
    assert handler["filters"] == ["redact_secrets"]
