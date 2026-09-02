"""Issuing, resending, revoking and consuming an invitation.

Three properties have to hold at once, and together they decide the token's
shape:

* **Only `token_hash` is stored.** A stolen database must yield no usable link.
* **A resend does not rotate the token.** A link an owner already sent over
  another channel keeps working, and `Copiar enlace` on a row whose email failed
  five times has to produce the *same* link the invitee may already hold.
* **The token never reaches a path segment.** It travels in the `#fragment` of
  the link and in a request body, and nowhere else.

So the token is *derived*, never stored: an HMAC over the invitation's own id
under the server secret. The server can re-derive it whenever an owner asks for
it; a database without the secret cannot. `token_hash` is what the lookup
matches on and what the unique index is built over.

The token also carries its own tenant, which is what lets the anonymous accept
flow **pin first and read second** without a second unpinned lookup: a forged
tenant simply finds no invitation under that pin.
"""

import base64
import hashlib
import hmac
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.models import Invitation, InvitationStatus, Role

SIGNATURE_BYTES = 24


def _signature(invitation_id):
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"invitation:{invitation_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()[:SIGNATURE_BYTES]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def token_for(invitation):
    """The link's token: tenant, invitation and the signature that proves both."""
    return (
        f"{uuid.UUID(str(invitation.tenant_id)).hex}."
        f"{uuid.UUID(str(invitation.id)).hex}."
        f"{_signature(invitation.id)}"
    )


def hash_token(token):
    """What is stored, and what the lookup matches on."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parts(token):
    """The tenant and invitation a token claims, or None if it is not one."""
    if not token or token.count(".") != 2:
        return None
    tenant_hex, invitation_hex, _signature_part = token.split(".")
    try:
        return uuid.UUID(hex=tenant_hex), uuid.UUID(hex=invitation_hex)
    except ValueError:
        return None


def default_expiry():
    return timezone.now() + timedelta(days=settings.BOTICA_INVITATION_TTL_DAYS)


def find_by_token(token):
    """The invitation a token names, inside whatever pin is already open.

    The comparison is against the stored hash, so a token whose signature does
    not check out matches no row -- there is no branch here that leaks which
    half of the token was wrong.
    """
    claimed = parts(token)
    if claimed is None:
        return None
    _tenant_id, invitation_id = claimed
    return (
        Invitation.objects.select_related("tenant", "location")
        .filter(id=invitation_id, token_hash=hash_token(token))
        .first()
    )


class InvitationRefused(Exception):
    """Why a token cannot be consumed, in the words the accept screen shows."""

    def __init__(self, message, *, reason):
        super().__init__(message)
        self.reason = reason


def check_consumable(invitation):
    """Three route-scope refusals, each with the same next step (§B.10.2).

    A revoked invitation is *not* told apart from an unknown one: saying so
    would confirm the address to whoever holds a link they should not.
    """
    if invitation is None:
        raise InvitationRefused("No reconocemos esta invitación.", reason="unknown")
    if invitation.status == InvitationStatus.ACCEPTED:
        raise InvitationRefused("Esta invitación ya fue usada.", reason="accepted")
    if invitation.status == InvitationStatus.REVOKED:
        raise InvitationRefused("No reconocemos esta invitación.", reason="revoked")
    if invitation.expires_at <= timezone.now():
        raise InvitationRefused(
            "Esta invitación venció el "
            f"{timezone.localtime(invitation.expires_at):%d/%m}.",
            reason="expired",
        )
    return invitation


def role_label(role):
    return dict(Role.choices).get(role, role)
