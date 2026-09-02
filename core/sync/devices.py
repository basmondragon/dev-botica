"""The device credential, the claim, and the three freshness stamps.

**A `/api/sync/*` call carries the session and the key, and neither alone is
sufficient** (A4, rule 6). The session resolves the tenant and the identity; the
key resolves the device and therefore the location. The location is never a
request parameter -- not a query string, not a body field, not a header -- and
`resolve` is the only place a location enters a sync request.

There is deliberately **no unpinned resolver** for the device key, even though
`core.tenancy` offers one. A resolver exists for a request that cannot know its
tenant yet; a sync request always has a session, so the key is looked up inside
the pin like any other row. Opening the system's one audited hole a second time
for a lookup that does not need it would cost the property that makes the hole
auditable at all.
"""

import hashlib
import secrets

from django.utils import timezone
from ninja.errors import HttpError

from core.models import Device, DeviceStatus

#: 32 bytes of `secrets` output, hex-encoded and prefixed so a key found in a
#: log or a support ticket is recognisable for what it is.
KEY_PREFIX = "bkd_"
KEY_BYTES = 32


class DeviceRefused(HttpError):
    """A sync call whose key resolves to no active device.

    It is a 401 and not a 403: the credential is the thing that failed, and a
    till reading `403` would tell a cashier they lack permission when what
    happened is that the office revoked the equipment.
    """

    def __init__(self, message):
        super().__init__(401, message)


def mint():
    """A new key and its hash. The plaintext is returned to the caller once."""
    key = KEY_PREFIX + secrets.token_hex(KEY_BYTES)
    return key, hash_key(key)


def allocate_code(tenant_id, location) -> str:
    """The short code a sale number is composed from (`C3-4821`).

    Derived from the sede's own code and the next free ordinal, so it reads as
    the sede it belongs to rather than as a serial. It is network-wide unique,
    because it is one half of a number that must not repeat anywhere in the
    network -- so a collision widens the prefix rather than reusing a code.
    """
    from core.models import Device

    taken = set(
        Device.objects.filter(tenant_id=tenant_id).values_list("code", flat=True)
    )
    stem = (location.code or "X").upper()
    # The initial first, because that is what the handoff draws — `Venta
    # C3-4821` is Chapinero's third till. Then the whole sede code, which is
    # already unique in the network, rather than every prefix in between: a
    # network of `S01`..`S19` would otherwise produce `S1`, `S01`, `S031`, three
    # different shapes for the same thing.
    for prefix in (stem[:1], stem):
        for ordinal in range(1, 100):
            candidate = f"{prefix}{ordinal}"[:16]
            if candidate not in taken:
                return candidate
    # A hundred tills at one counter. The full code plus a count terminates.
    return f"{stem}{len(taken) + 1}"[:16]


def hash_key(key: str) -> str:
    """`sha256`, and deliberately not a password hash.

    A password hash is slow because a password is guessable; this is 256 bits of
    `secrets` output, so there is no dictionary to slow down. A bcrypt on the
    hot sync path would cost every till every eight seconds to protect against
    an attack that cannot be mounted.
    """
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def resolve(request):
    """The device this request is made from, or the refusal a till renders.

    Read **inside the pin**, so a key belonging to another network resolves to
    nothing and is answered exactly as a bad key is.
    """
    key = (request.headers.get("X-Botica-Device-Key") or "").strip()
    if not key:
        raise DeviceRefused(
            "Este navegador no está registrado como equipo de venta. Regístrelo "
            "desde el mostrador para descargar el catálogo."
        )
    device = Device.objects.filter(
        tenant_id=request.tenant_id, device_key_hash=hash_key(key)
    ).first()
    if device is None:
        raise DeviceRefused(
            "No reconocemos este equipo. Vuelva a registrarlo desde el mostrador."
        )
    # Refused **before any predicate is built**: a revoked device must not reach
    # the code that decides what it may read, or the security check would only
    # ever be exercised on the happy path.
    if device.status != DeviceStatus.ACTIVE:
        raise DeviceRefused("este equipo fue dado de baja")
    return device


def touch(device, request, *, pulled=False, pushed=False):
    """Record what this call told us about the device, and answer the clock.

    Every sync call carries the device's wall clock, its application version and
    whether its browser has granted persistent storage, so skew is measured on
    every interaction and needs no endpoint of its own.

    **The skew is stored and displayed and never used to correct anything.**
    `occurred_at` is the till's and is what the cashier will swear to;
    `recorded_at` is the server's and is what every report reads (§5 rule 4).
    """
    now = timezone.now()
    fields = ["last_seen_at", "updated_at"]
    device.last_seen_at = now

    reported = _clock(request)
    if reported is not None:
        device.clock_skew_ms = int((reported - now).total_seconds() * 1000)
        fields.append("clock_skew_ms")

    version = (request.headers.get("X-Botica-App-Version") or "").strip()[:32]
    if version and version != device.app_version:
        device.app_version = version
        fields.append("app_version")

    persisted = _persisted(request)
    # **Null is "not yet reported", never false.** A browser that has not
    # answered `navigator.storage.persist()` has not refused it, and writing
    # `false` for silence would put a warning badge on every device that has
    # simply not got there yet.
    if persisted is not None and persisted != device.storage_persisted:
        device.storage_persisted = persisted
        fields.append("storage_persisted")

    if pulled:
        device.last_synced_at = now
        fields.append("last_synced_at")
    if pushed:
        device.last_pushed_at = now
        fields.append("last_pushed_at")

    device.save(update_fields=fields)
    return now


def _clock(request):
    """The device's own wall clock, as it reported it."""
    from django.utils.dateparse import parse_datetime

    raw = (request.headers.get("X-Botica-Device-Clock") or "").strip()
    if not raw:
        return None
    try:
        parsed = parse_datetime(raw)
    except ValueError:
        return None
    if parsed is None:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def _persisted(request):
    raw = (request.headers.get("X-Botica-Storage-Persisted") or "").strip().lower()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    return None


def skew_seconds(device):
    """The reported skew in whole seconds, or None where none was reported."""
    if device.clock_skew_ms is None:
        return None
    return int(device.clock_skew_ms / 1000)
