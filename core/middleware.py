"""Request-scoped middleware: the correlation id, the pin, and the admin gate."""

import contextvars
import ipaddress
import uuid
from functools import lru_cache

from django.conf import settings
from django.db import transaction
from django.http import HttpResponseNotFound

from core.tenancy import NO_TENANT, pin_tenant

SESSION_TENANT_KEY = "botica_tenant_id"
SESSION_USER_KEY = "botica_user_id"

ADMIN_PREFIXES = ("/admin", "/static/admin")
EDGE_HEADER = "HTTP_X_BOTICA_EDGE"
FORWARDED_HEADER = "HTTP_X_FORWARDED_FOR"

#: The id §B.10.3 asks every route-scope error to print, and the one
#: `audit_log.request_id` stamps. One value per request, readable from anywhere.
request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "botica_request_id", default=""
)


class RequestIdMiddleware:
    """One correlation id per request, echoed on the response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        given = request.META.get("HTTP_X_REQUEST_ID", "")
        value = given if _is_safe_id(given) else f"req_{uuid.uuid4().hex[:8]}"
        token = request_id.set(value)
        request.request_id = value
        try:
            response = self.get_response(request)
        finally:
            request_id.reset(token)
        response["X-Request-Id"] = value
        return response


def _is_safe_id(value):
    return bool(value) and len(value) <= 64 and value.replace("_", "").isalnum()


class TenantMiddleware:
    """Pin every request's transaction to its resolved tenant before any query.

    A request that resolves no tenant runs with no pin and therefore reads zero
    rows -- not another tenant's.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_id = _session_tenant(request)
        with pin_tenant(tenant_id, user_id=_session_user(request)):
            request.tenant_id = None if tenant_id == NO_TENANT else tenant_id
            response = self.get_response(request)
            if response.status_code >= 400:
                transaction.set_rollback(True)
        return response


def _session_tenant(request):
    raw = request.session.get(SESSION_TENANT_KEY)
    return _as_uuid(raw, NO_TENANT)


def _session_user(request):
    return _as_uuid(request.session.get(SESSION_USER_KEY), None)


def _as_uuid(raw, fallback):
    if not raw:
        return fallback
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return fallback


@lru_cache(maxsize=8)
def _parse(entries):
    """Parse the configured admin network allowlist."""
    parsed = []
    for entry in entries:
        try:
            parsed.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as error:
            raise RuntimeError(
                f"BOTICA_ADMIN_ALLOWED_IPS carries {entry!r}, which is not an "
                "address or CIDR."
            ) from error
    return parsed


def _networks():
    return _parse(tuple(settings.BOTICA_ADMIN_ALLOWED_IPS))


def is_admin_path(path):
    """Whether a path targets or reveals the platform-admin console."""
    return any(
        path == prefix or path.startswith(prefix + "/") for prefix in ADMIN_PREFIXES
    )


def _split_host(value):
    value = value.strip().lower()
    if value.startswith("[") and ":" in value:
        close = value.find("]")
        if close != -1:
            return value[: close + 1].rstrip("."), value[close + 1 :].lstrip(":")
    head, separator, tail = value.rpartition(":")
    if separator and tail.isdigit() and ":" not in head:
        return head.rstrip("."), tail
    return value.rstrip("."), ""


def _host_names(value):
    host, port = _split_host(value)
    if not host:
        return set()
    names = {host}
    if port:
        names.add(f"{host}:{port}")
    if host.startswith("[") and host.endswith("]") and ":" in host:
        names.add(host[1:-1])
    elif ":" in host:
        names.add(f"[{host}]")
    return names


def _host_allowed(request):
    allowed = getattr(settings, "BOTICA_ADMIN_ALLOWED_HOSTS", [])
    if not allowed:
        return True
    asked = _host_names(
        request.META.get("HTTP_HOST") or request.META.get("SERVER_NAME") or ""
    )
    if not asked:
        return False
    return any(asked & _host_names(entry) for entry in allowed)


def admin_console_reachable(request):
    """Whether the platform-admin console is reachable for a request.

    Nothing in Django admin is ever shown to a tenant user, and the surest way to
    keep it that way is that the public edge cannot reach it at all.
    """
    if request.META.get(EDGE_HEADER) or request.META.get(FORWARDED_HEADER):
        return False
    if not _host_allowed(request):
        return False
    peer = (request.META.get("REMOTE_ADDR") or "").strip()
    if not peer:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in _networks())


class AdminConsoleMiddleware:
    """Restrict the platform-admin console to approved local requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_admin_path(request.path_info) and not admin_console_reachable(request):
            return HttpResponseNotFound("Not found.")
        return self.get_response(request)
