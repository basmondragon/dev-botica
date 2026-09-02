"""The `devices` fixture: the tills, on every profile.

`devices` is S2's table and S2 alone writes it (ledger), so no other stage can
claim a till at a sede -- and S4's fixture depends on this one, because
`sales.number` is composed from the device's own identity, which is what makes a
seeded ticket read like the handoff's `Venta C3-4821` rather than like a row
from a generator.

**The rule is one till per sede plus a second at the busiest one**, so every
profile's device count is derived rather than chosen and no check has to
hand-edit `devices` to reach the fleet it needs. `scale` is the one profile that
departs from it, and deliberately: it carries **two per sede over twenty sedes**
because forty devices at an eight-second interval is exactly the fleet *The poll
schedule* does its arithmetic on, and a figure taken on trust is not one anybody
can measure.

**One device is deliberately silent.** Its `last_synced_at` is three days old,
past `stale_device_hours`, so the office list renders `Sin sincronizar hace 3
días` and the stale-device job has a row to raise. A fixture where every device
is healthy never exercises the question support is actually asked, which is
*"which till has not synced, and since when"*.
"""

import hashlib
from datetime import timedelta

from django.utils import timezone

from core.demo.registry import register
from core.models import Device, DeviceStatus, Location, Role, User

#: The second till goes to the busiest sede, which on the handoff's network is
#: Chapinero. On `scale` and `minimal` the busiest sede is simply the first.
BUSIEST = {"default": "CHA", "young": "CHA", "cold": "CHA"}

#: How far behind the server the silent one is. Three days reads as
#: `Sin sincronizar hace 3 días` on §B.9.1's ladder and is past the 48-hour
#: default, so it is both a badge and a job's input.
SILENT_DAYS = 3

#: What a healthy seeded device's `last_synced_at` is behind `now`, so the sync
#: chip on a seeded tenant reads **`Sincronizado hace 4 s`** exactly as the
#: handoff draws it.
FRESH_SECONDS = 4

APP_VERSIONS = ("0.1.0", "0.1.0", "0.1.0", "0.1.0", "0.0.9")


def _plan(context):
    """`(location_code, label)` per device, per profile.

    `minimal` gets one -- **and it is not the silent one**, because a fixture
    whose only device is stale fails every S0 check that needs a healthy till,
    for a reason S0 did not cause.
    """
    codes = _codes(context)
    if not codes:
        return []
    if context.profile == "scale":
        return sorted((code, label) for code in codes for label in ("Caja 1", "Caja 2"))
    busiest = BUSIEST.get(context.profile, codes[0])
    plan = [(code, "Caja 1") for code in codes]
    if len(codes) > 1:
        plan.append((busiest, "Caja 2"))
    return sorted(plan)


def _codes(context):
    return list(
        Location.objects.filter(tenant_id=context.tenant_id)
        .order_by("code")
        .values_list("code", flat=True)
    )


def _codes_for(plan):
    """`C1`, `C2`, `K1` ... one per planned till, and unique network-wide.

    The same rule the claim path uses: the sede's initial, and the whole sede
    code where that collides. On the handoff's six sedes every initial is free,
    so the drawn `Venta C3-4821` reads back; on `scale`'s twenty `S01`-`S19` the
    first sede takes `S1` and the rest take `S021`, `S031` — which is what
    happens when a rule is derived rather than chosen.
    """
    taken: set[str] = set()
    codes: dict[tuple[str, str], str] = {}
    for sede, label in plan:
        stem = sede.upper()
        ordinal = label.split(" ")[-1]
        chosen = next(
            (
                f"{prefix}{ordinal}"[:16]
                for prefix in (stem[:1], stem)
                if f"{prefix}{ordinal}"[:16] not in taken
            ),
            f"{stem}{len(taken) + 1}"[:16],
        )
        codes[(sede, label)] = chosen
        taken.add(chosen)
    return codes


def _silent(plan, profile):
    """Which of the planned devices is the quiet one.

    One on the small profiles, three on `scale` -- which is the fleet *The poll
    schedule* does its arithmetic on -- and **none** on `minimal`.
    """
    if profile == "minimal" or not plan:
        return set()
    if profile == "scale":
        # Three of forty, spread across the network rather than adjacent, so
        # the office list's warning badge is not one clump somebody scrolls past.
        return {plan[index] for index in (4, 19, 33) if index < len(plan)}
    return {plan[-1]}


def _key_hash(context, code, label):
    """A deterministic hash, so a re-seed writes the same row.

    The plaintext key is never derivable from this and is never printed: a
    seeded device is a row the office list renders, not a browser anybody signs
    in from. A demo till claims itself the way a real one does.
    """
    return hashlib.sha256(
        f"{context.tenant_id}:{code}:{label}:seed".encode("utf-8")
    ).hexdigest()


def owned_ids(context):
    """Exactly the rows this fixture writes, so the guard can refuse any other."""
    return {
        "devices": {
            context.uid("devices", f"{code}:{label}") for code, label in _plan(context)
        }
    }


def build(context):
    """Write the tills, inside the pin the command already opened."""
    now = timezone.now()
    plan = _plan(context)
    silent = _silent(plan, context.profile)
    codes = _codes_for(plan)

    by_code = {
        location.code: location
        for location in Location.objects.filter(tenant_id=context.tenant_id)
    }
    # Whoever would have claimed it: the sede's own cashier where there is one,
    # and the owner otherwise -- which is `minimal`, whose single device is
    # enrolled from the device list, the path scope 2 already allows rather than
    # an exception cut for the fixture.
    cashiers = {
        person.location_id: person
        for person in User.objects.filter(
            tenant_id=context.tenant_id, role=Role.CASHIER
        )
    }
    owner = User.objects.filter(tenant_id=context.tenant_id, role=Role.OWNER).first()

    written = 0
    for index, (code, label) in enumerate(plan):
        location = by_code.get(code)
        if location is None:
            continue
        quiet = (code, label) in silent
        seen = (
            now - timedelta(days=SILENT_DAYS) if quiet else now - timedelta(seconds=1)
        )
        synced = (
            now - timedelta(days=SILENT_DAYS)
            if quiet
            else now - timedelta(seconds=FRESH_SECONDS)
        )
        row_id = context.uid("devices", f"{code}:{label}")
        device = Device.objects.filter(id=row_id).first() or Device(id=row_id)
        device.tenant_id = context.tenant_id
        device.location = location
        device.label = label
        # The code `sales.number` is composed from, so a seeded ticket reads
        # like the handoff's `Venta C3-4821`.
        device.code = codes[(code, label)]
        device.device_key_hash = _key_hash(context, code, label)
        device.status = DeviceStatus.ACTIVE
        device.last_seen_at = seen
        # Never ahead of the server's clock: a seeded `last_synced_at` in the
        # future renders `hace -3 s`, and a stranger reading the office list
        # would have found the fixture.
        device.last_synced_at = synced
        device.last_pushed_at = synced - timedelta(seconds=2)
        # A skew inside the warning threshold on every till but one, so the
        # panel's skew line has somewhere to render and the rest stay quiet.
        device.clock_skew_ms = 240_000 if index == 1 else 120
        device.storage_persisted = False if index == 2 else True
        device.app_version = APP_VERSIONS[index % len(APP_VERSIONS)]
        device.enrolled_by_user = cashiers.get(location.id) or owner
        device.enrolled_at = now - timedelta(days=40 + index)
        device.revoked_at = None
        device.revoked_by_user = None
        device.save()
        written += 1
    context.wrote("devices", written)


register(
    "devices",
    tables=("devices",),
    requires=("identity",),
    build=build,
    owned_ids=owned_ids,
)
