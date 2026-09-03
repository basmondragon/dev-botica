"""The transactional email adapter (architecture §10).

Invitations at S0; expiry alerts and scheduled reports later. **Delivery failure
never blocks the operation that triggered it** -- an invitation whose email
bounced five times is still a valid invitation, because email is a delivery
channel and not the credential.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

SENT = "sent"
NOT_CONFIGURED = "not_configured"

UNDELIVERED_BACKENDS = frozenset(
    {
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.dummy.EmailBackend",
        "django.core.mail.backends.filebased.EmailBackend",
    }
)


class DeliveryFailed(Exception):
    """The relay refused or could not be reached. The caller retries; it does not
    roll back whatever produced the message."""


def is_configured():
    """Whether a message posted from here can reach somebody's inbox."""
    backend = settings.EMAIL_BACKEND
    if backend in UNDELIVERED_BACKENDS:
        return False
    return not (backend == settings.SMTP_BACKEND and not settings.EMAIL_HOST)


def _send(template, to, subject, context):
    if settings.EMAIL_BACKEND == settings.SMTP_BACKEND and not settings.EMAIL_HOST:
        logger.warning("mail has no relay: %s for %s was not sent", template, to)
        return NOT_CONFIGURED

    context = {"app_url": settings.BOTICA_APP_URL, "to_email": to, **context}
    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string(f"mail/{template}.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    message.attach_alternative(
        render_to_string(f"mail/{template}.html", context), "text/html"
    )
    try:
        message.send()
    except Exception as error:
        logger.error("mail failed: %s for %s: %s", template, to, error)
        raise DeliveryFailed(str(error)) from error

    if not is_configured():
        logger.warning(
            "mail is not configured: %s for %s was printed, not sent", template, to
        )
        return NOT_CONFIGURED
    logger.info("mail sent: %s for %s", template, to)
    return SENT


def send_invitation(
    *, email, accept_url, tenant_name, role_label, location_name, invited_by, expires_at
):
    """The link an invited person needs, sent to the address it was issued for."""
    return _send(
        "invitation",
        email,
        f"Le invitaron a {tenant_name} en Botica",
        {
            "accept_url": accept_url,
            "tenant_name": tenant_name,
            "role_label": role_label,
            "location_name": location_name,
            "invited_by": invited_by,
            "expires_at": expires_at,
        },
    )


def send_expiry_digest(
    *, email, tenant_name, location_name, horizon_days, lines, run_date
):
    """What is expiring at one sede, to whoever asked to be told.

    **It reports and never acts.** §12 fixes that Botica surfaces the state and
    records the pharmacy's decision, so this message names lots and quantities
    and asks a person to decide -- there is no link in it that writes anything
    off.
    """
    expired = [line for line in lines if line["expired"]]
    return _send(
        "expiry_digest",
        email,
        f"{tenant_name} · {location_name}: {len(lines)} lotes por vencer",
        {
            "tenant_name": tenant_name,
            "location_name": location_name,
            "horizon_days": horizon_days,
            "lines": lines,
            "expired_count": len(expired),
            "run_date": run_date,
        },
    )


def send_stale_shifts(*, recipients, tenant_name, location_name, run_date, shifts):
    """Turnos left open more than a day, to the network's owner and
    administrators.

    **It reports and never closes.** Closing a cash session without a count
    destroys the count, which is the one number the session exists to produce
    (§6), so this message names the tills and asks a person to decide -- there is
    no link in it that closes anything.

    One message per recipient rather than one with several addressees: a
    droguería's owner and its two administrators are three people, not a mailing
    list, and a `To` header naming all three publishes their addresses to each
    other.
    """
    outcome = NOT_CONFIGURED
    for email in recipients:
        outcome = _send(
            "stale_shifts",
            email,
            f"{tenant_name} · {location_name}: {len(shifts)} turnos sin cerrar",
            {
                "tenant_name": tenant_name,
                "location_name": location_name,
                "run_date": run_date,
                "shifts": shifts,
            },
        )
    return outcome
