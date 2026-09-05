"""Background jobs.

One job at S0. The queue and the worker exist so that later stages add jobs and
nothing else -- but shipping the chain with zero jobs would mean the first stage
to need one is also the first to find out whether pinning inside a worker works.
"""

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from procrastinate import RetryStrategy
from procrastinate import exceptions as queue_exceptions
from procrastinate.contrib.django import app

from core import mail
from core.models import Invitation, InvitationStatus, Role
from core.tenancy import pinned_job

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = settings.BOTICA_INVITATION_MAX_ATTEMPTS


@app.task(
    name="send_invitation_email",
    queue="mail",
    pass_context=True,
    retry=RetryStrategy(
        max_attempts=MAX_ATTEMPTS,
        wait=5,
        exponential_wait=2,
        retry_exceptions=(mail.DeliveryFailed,),
    ),
)
def send_invitation_email(context, *, tenant_id, invitation_id, token, issued_at):
    """Deliver one invitation link.

    The payload carries `tenant_id` and the job pins before touching anything
    (ledger rule 6, context three). `issued_at` is part of the idempotency key
    `invitation:{id}:{issued_at}`: a duplicate enqueue is a no-op, and a resend
    after a revoke-and-reissue is a new invitation and therefore a new key.

    On exhaustion the row's `last_delivery_error` is stamped and Personas renders
    **Envío fallido** with `Reenviar` and `Copiar enlace` beside it. **The
    invitation stays valid** -- an owner in a droguería will send the link over
    WhatsApp without being asked.
    """
    del issued_at  # part of the queueing lock, not of the work
    failure: mail.DeliveryFailed | None = None

    with pinned_job({"tenant_id": tenant_id}):
        invitation = (
            Invitation.objects.select_related("tenant", "location", "invited_by")
            .filter(id=invitation_id)
            .first()
        )
        if invitation is None:
            logger.info("invitation %s is gone; nothing to deliver", invitation_id)
            return
        if invitation.status != InvitationStatus.PENDING:
            logger.info(
                "invitation %s is %s; not delivering",
                invitation_id,
                invitation.status,
            )
            return
        try:
            mail.send_invitation(
                email=invitation.email,
                accept_url=invitation.accept_url(token),
                tenant_name=invitation.tenant.name,
                role_label=dict(Role.choices).get(invitation.role, invitation.role),
                location_name=(invitation.location.name if invitation.location else ""),
                invited_by=(
                    invitation.invited_by.name if invitation.invited_by else ""
                ),
                expires_at=invitation.expires_at,
            )
        except mail.DeliveryFailed as error:
            # Caught, not raised, so this transaction still commits: an
            # exception leaving the pinned block rolls it back, and the stamp
            # below would roll back with it -- leaving the roster with no
            # `Envío fallido` row and an owner with nothing to press.
            failure = error
        else:
            if invitation.last_delivery_error:
                Invitation.objects.filter(id=invitation.id).update(
                    last_delivery_error=""
                )

    if failure is None:
        return

    if _attempts_so_far(context) + 1 >= MAX_ATTEMPTS:
        # Its own transaction, so the stamp survives the retry that follows.
        with pinned_job({"tenant_id": tenant_id}):
            Invitation.objects.filter(id=invitation_id).update(
                last_delivery_error=str(failure)[:500]
            )
    raise failure


def _attempts_so_far(context):
    """How many attempts this job has already spent, however it was invoked."""
    return getattr(getattr(context, "job", None), "attempts", MAX_ATTEMPTS - 1)


def enqueue_invitation_email(invitation, token):
    """Queue one delivery under its idempotency key.

    `invitation:{id}:{issued_at}` -- **a duplicate enqueue is a no-op**, which
    is what the queue's own unique index on the lock says and what this catch
    turns from an error into the intended silence. A resend stamps
    `updated_at`, so it is a new key; a revoke-and-reissue is a new invitation
    and therefore a new key too.
    """
    issued = int(invitation.updated_at.timestamp() * 1_000_000)
    try:
        with transaction.atomic():
            send_invitation_email.configure(
                queueing_lock=f"invitation:{invitation.id}:{issued}",
            ).defer(
                tenant_id=str(invitation.tenant_id),
                invitation_id=str(invitation.id),
                token=token,
                issued_at=issued,
            )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        logger.info(
            "invitation %s is already queued for delivery under this key",
            invitation.id,
        )


# Procrastinate's Django integration discovers `<app>/tasks.py` and nothing
# deeper, so a stage whose jobs live in their own module registers them by
# importing it here. One import per stage, and the worker's task list stays a
# thing somebody can read in one place.
from core.catalog import jobs  # noqa: E402,F401  -- registers S1's INVIMA sweep
from core.sync import jobs as sync_jobs  # noqa: E402,F401  -- S2's stale-device check
from core.inventory import jobs as inventory_jobs  # noqa: E402,F401  -- S3's three
from core.counter import jobs as counter_jobs  # noqa: E402,F401  -- S4's stale-shift notice
from core.fiscal import jobs as fiscal_jobs  # noqa: E402,F401  -- S5's delivery and sweep
from core.purchasing import jobs as purchasing_jobs  # noqa: E402,F401  -- S6's five
from core.pricing import jobs as pricing_jobs  # noqa: E402,F401  -- S7's two
