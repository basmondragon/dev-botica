"""Invite-only authentication on django-allauth headless.

Identity is carried by the session, not re-read across the pin. Sign-in resolves
`user_id`, `tenant_id`, `role` and `platform_admin` once, through the unpinned
resolution registry, and stores them in the session. On every later request the
permission dependency re-reads the acting user's row *inside* the pin, so a
suspension or a role change takes effect on the next request rather than at the
next sign-in.
"""

import logging

from allauth.account.adapter import DefaultAccountAdapter, get_adapter
from allauth.account.auth_backends import AuthenticationBackend
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.exceptions import PermissionDenied, ValidationError
from django.dispatch import receiver

from core.middleware import SESSION_TENANT_KEY, SESSION_USER_KEY
from core.models import Tenant, TenantStatus, User, UserStatus
from core.tenancy import NO_TENANT, repin, resolve

logger = logging.getLogger(__name__)


def _attempted(credentials):
    """The address an attempt was made against, for a log line. Never a password."""
    return credentials.get("email") or credentials.get("username") or "(sin correo)"


def _client_ip(request):
    if request is None:
        return "(sin petición)"
    try:
        return get_adapter().get_client_ip(request)
    except PermissionDenied:
        return "(sin dirección)"


class InviteOnlyAccountAdapter(DefaultAccountAdapter):
    """No self-signup path exists, and no registration endpoint is exposed."""

    def is_open_for_signup(self, request):
        return False

    def pre_authenticate(self, request, **credentials):
        try:
            super().pre_authenticate(request, **credentials)
        except ValidationError:
            logger.warning(
                "sign-in throttled: %s from %s",
                _attempted(credentials),
                _client_ip(request),
            )
            raise

    def authenticate(self, request, **credentials):
        """Name the refusal. Never `Credenciales inválidas`.

        §B.8.4·5 fixes the messages the sign-in card shows and a generic one is
        explicitly not among them: it tells an attacker nothing and tells a
        cashier nothing either. This is a deliberate trade, and it is why
        `ACCOUNT_PREVENT_ENUMERATION` is off -- on an invite-only tool used by a
        droguería's own staff, telling a cashier that their address is unknown
        is worth more than hiding which addresses exist.
        """
        user = super().authenticate(request, **credentials)

        email = credentials.get("email") or credentials.get("username")
        password = credentials.get("password")
        if not email or not password:
            return user

        candidate = user or User.objects.filter(email__iexact=email).first()
        if candidate is None:
            raise ValidationError(
                "No encontramos una cuenta con ese correo.",
                code="account_unknown",
            )
        # The state checks run whether or not the credential matched, and
        # before the user is handed back: allauth's own login flow refuses an
        # inactive account with a bare 401 and no message, which is exactly the
        # silence this method exists to prevent.
        if candidate.status != UserStatus.ACTIVE:
            raise ValidationError(
                "Su cuenta está suspendida. Pida a la administradora de su "
                "droguería que la reactive.",
                code="account_inactive",
            )
        if (
            candidate.tenant_id
            and Tenant.objects.filter(
                id=candidate.tenant_id, status=TenantStatus.SUSPENDED
            ).exists()
        ):
            raise ValidationError(
                "Esta droguería está suspendida. Escriba a soporte para reactivarla.",
                code="tenant_suspended",
            )
        if user is not None:
            return user
        # The fifth case §B.8.4·5 does not enumerate, because the card it draws
        # has no password field. This one does, so the state exists and it is
        # named rather than folded into the unknown-address message: a cashier
        # who mistyped their password must not be told their account is gone.
        raise ValidationError(
            "La contraseña no coincide con esa cuenta.",
            code="wrong_password",
        )


class TenantAuthenticationBackend(AuthenticationBackend):
    """Resolve and pin the tenant before the credential check.

    `users` is behind RLS, so there is nothing to authenticate against until the
    transaction is pinned -- which is why sign-in is the one lookup registered in
    the unpinned resolution registry.
    """

    def authenticate(self, request, **credentials):
        email = credentials.get("email") or credentials.get("username")
        if email:
            answer = resolve("sign_in", email)
            if answer is None:
                return None
            repin(answer.tenant_id, user_id=answer.subject_id)
        return super().authenticate(request, **credentials)


@receiver(user_login_failed)
def _log_failed_sign_in(sender, credentials, request=None, **kwargs):
    logger.warning(
        "sign-in failed: %s from %s", _attempted(credentials), _client_ip(request)
    )


@receiver(user_logged_in)
def _record_identity_on_session(sender, request, user, **kwargs):
    """Write the resolved identity to the session once, at sign-in."""
    request.session[SESSION_TENANT_KEY] = str(user.tenant_id or NO_TENANT)
    request.session[SESSION_USER_KEY] = str(user.id)
