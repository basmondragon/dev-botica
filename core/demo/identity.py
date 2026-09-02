"""S0's own fixture: `tenants`, `locations`, `users` and `invitations`.

The identity fixture is the root every later stage's fixture declares a
dependency on. It is the same under `default`, `young` and `cold` -- those three
profiles differ only in what the *later* stages build on top of it -- so a check
written against the numbers here holds on all three.

Below the tenant row it writes through S0's own creation paths rather than by
hand, so the seed **sequences** this stage's writers instead of becoming a
second one that drifts from them the first time a column moves.
"""

from datetime import timedelta

from django.utils import timezone

from core import audit, invitations as invitation_service
from core.demo.registry import register
from core.models import (
    AuditAction,
    Invitation,
    InvitationStatus,
    Location,
    LocationType,
    Role,
    Tenant,
    TenantStatus,
    User,
    UserStatus,
)

#: Synthetic, and self-evidently so. The guard confines this command to tenants
#: whose slug begins `demo-`, which no provisioned network can acquire.
DEMO_PASSWORD = "botica-demo-2026"

LA_45 = {
    "name": "Droguerías La 45",
    "legal_name": "Droguerías La 45 S.A.S.",
    "nit": "901.245.778-3",
}

#: The six sedes the handoff draws. **All six sell** -- the prototype draws a
#: `Sede · Todas · 6` chip and stock in each -- so the `location_type` enum's
#: second value is exercised by `scale`, whose twenty locations carry the
#: network's one `warehouse`, rather than by turning one of the six drawn sedes
#: into a bodega no later fixture can sell from.
LA_45_SEDES = [
    ("CHA", "Chapinero", "Bogotá", "Calle 63 # 13-45", "601 745 2210"),
    ("KEN", "Kennedy", "Bogotá", "Carrera 78 # 38-12 Sur", "601 745 2211"),
    ("SUB", "Suba", "Bogotá", "Calle 145 # 91-30", "601 745 2212"),
    ("RES", "Restrepo", "Bogotá", "Carrera 20 # 18-06 Sur", "601 745 2213"),
    ("BOS", "Bosa", "Bogotá", "Calle 65 Sur # 78-41", "601 745 2214"),
    ("USM", "Usme", "Bogotá", "Carrera 3 Este # 89-15 Sur", "601 745 2215"),
]

#: Nine people: one owner, two administrators -- one of them Marcela Ríos, whom
#: the user footer draws as `Marcela Ríos · Administradora` -- and six cashiers,
#: one homed at each sede, with Andrés Peña at Chapinero for the drawn
#: `Andrés Peña · Mostrador · Chapinero`. The other five are named too, so a
#: later stage naming a till operator has a person to name rather than a uuid.
LA_45_PEOPLE: list[tuple[str, str, str, str | None]] = [
    ("Beatriz Aguirre", "beatriz.aguirre@la45.co", Role.OWNER, None),
    ("Marcela Ríos", "marcela.rios@la45.co", Role.ADMIN, None),
    ("Hernán Salcedo", "hernan.salcedo@la45.co", Role.ADMIN, None),
    ("Andrés Peña", "andres.pena@la45.co", Role.CASHIER, "CHA"),
    ("Liliana Torres", "liliana.torres@la45.co", Role.CASHIER, "KEN"),
    ("Jhon Castaño", "jhon.castano@la45.co", Role.CASHIER, "SUB"),
    ("Yuly Mora", "yuly.mora@la45.co", Role.CASHIER, "RES"),
    ("Wilson Cárdenas", "wilson.cardenas@la45.co", Role.CASHIER, "BOS"),
    ("Diana Quintero", "diana.quintero@la45.co", Role.CASHIER, "USM"),
]

#: Four invitations, one per rendered state (§B.7.3), at four distinct addresses
#: so the partial unique index over the pending rows holds. **Vencida** doubles
#: as the check that expiry is derived at read time from `expires_at` rather
#: than stored as a fifth status nobody sweeps.
LA_45_INVITATIONS: list[tuple[str, str, str, str | None, int]] = [
    ("pending", "camila.rojas@la45.co", Role.CASHIER, "KEN", 7),
    ("expired", "oscar.beltran@la45.co", Role.CASHIER, "SUB", -2),
    ("revoked", "natalia.gil@la45.co", Role.ADMIN, None, 7),
    ("delivery_failed", "ruben.moreno@la45.co", Role.CASHIER, "BOS", 5),
]

#: The 43 audit rows, and the number is derivable rather than decorative: nine
#: people remain of eleven created, which is why `actor_email` is stamped rather
#: than joined.
LA_45_AUDIT_PLAN = [
    (AuditAction.CREATE, "invitations", 15),
    (AuditAction.CREATE, "users", 11),
    (AuditAction.SEND, "invitations", 3),
    (AuditAction.REVOKE, "invitations", 1),
    (AuditAction.UPDATE, "users", 9),
    (AuditAction.DELETE, "users", 2),
    (AuditAction.UPDATE, "tenants", 2),
]

RED_20 = {
    "name": "Red Vecina del Sur",
    "legal_name": "Red Vecina del Sur S.A.S.",
    "nit": "900.884.512-1",
}

MINIMAL = {
    "name": "Farmacia La Estrella",
    "legal_name": "Farmacia La Estrella S.A.S.",
    "nit": "901.002.334-7",
}

SLUGS = {
    "default": "demo-la-45",
    "young": "demo-la-45-young",
    "cold": "demo-la-45-cold",
    "scale": "demo-red-20",
    "minimal": "demo-minimal",
}

SCALE_CITIES = [
    "Bogotá",
    "Soacha",
    "Fusagasugá",
    "Girardot",
    "Villavicencio",
    "Ibagué",
    "Neiva",
    "Espinal",
    "Melgar",
    "Chía",
]


def slug_for(profile):
    return SLUGS[profile]


def sedes(profile):
    """`(code, name, city, address, phone, type)` per sede, for a later stage's
    fixture that has to know the network's shape **before any row exists**.

    S3 needs it: its plan is computed without touching the database, because the
    seed guard counts every fixture's owned ids before the first one runs. This
    is the same list `_sedes` returns and is public for that one reason.
    """
    return _sedes(profile)


def _network(profile):
    if profile == "scale":
        return RED_20
    if profile == "minimal":
        return MINIMAL
    return LA_45


def _sedes(profile):
    """`(code, name, city, address, phone, type)` per sede, per profile."""
    if profile == "minimal":
        return [
            (
                "EST",
                "La Estrella",
                "Medellín",
                "Calle 30 # 42-11",
                "604 322 1180",
                LocationType.STORE,
            )
        ]
    if profile == "scale":
        rows = []
        for index in range(1, 20):
            city = SCALE_CITIES[(index - 1) % len(SCALE_CITIES)]
            rows.append(
                (
                    f"S{index:02d}",
                    f"Sede {index:02d} · {city}",
                    city,
                    f"Carrera {index + 10} # {index * 3}-{index * 7 % 90:02d}",
                    f"601 900 {1000 + index}",
                    LocationType.STORE,
                )
            )
        # The network's one bodega -- and the only `warehouse` any profile builds.
        rows.append(
            (
                "BOD",
                "Bodega Sur",
                "Soacha",
                "Autopista Sur km 3",
                "601 900 1099",
                LocationType.WAREHOUSE,
            )
        )
        return rows
    return [
        (code, name, city, address, phone, LocationType.STORE)
        for code, name, city, address, phone in LA_45_SEDES
    ]


def _people(profile) -> list[tuple[str, str, str, str | None]]:
    """`(name, email, role, sede code or None)` per person, per profile."""
    if profile == "minimal":
        return [("Elena Restrepo", "elena.restrepo@laestrella.co", Role.OWNER, None)]
    if profile == "scale":
        rows: list[tuple[str, str, str, str | None]] = [
            ("Gustavo Pineda", "gustavo.pineda@redvecina.co", Role.OWNER, None)
        ]
        for index in range(1, 4):
            rows.append(
                (
                    f"Administradora {index}",
                    f"admin{index}@redvecina.co",
                    Role.ADMIN,
                    None,
                )
            )
        for index in range(1, 21):
            code = "BOD" if index == 20 else f"S{index:02d}"
            rows.append(
                (
                    f"Mostrador {index:02d}",
                    f"mostrador{index:02d}@redvecina.co",
                    Role.CASHIER,
                    code,
                )
            )
        return rows
    return LA_45_PEOPLE


def _invitations(profile) -> list[tuple[str, str, str, str | None, int]]:
    """Every fixture answers every profile: `minimal` returns empty explicitly
    rather than omitting the case, because a profile a fixture silently ignores
    is a profile whose screens nobody reviewed."""
    if profile == "minimal":
        return []
    if profile == "scale":
        return [
            ("pending", "nueva1@redvecina.co", Role.CASHIER, "S01", 7),
            ("expired", "nueva2@redvecina.co", Role.CASHIER, "S02", -2),
            ("revoked", "nueva3@redvecina.co", Role.ADMIN, None, 7),
            ("delivery_failed", "nueva4@redvecina.co", Role.CASHIER, "S03", 5),
        ]
    return LA_45_INVITATIONS


def _audit_plan(profile):
    if profile == "minimal":
        return [(AuditAction.UPDATE, "tenants", 1)]
    if profile == "scale":
        return [
            (AuditAction.CREATE, "invitations", 30),
            (AuditAction.CREATE, "users", 26),
            (AuditAction.SEND, "invitations", 6),
            (AuditAction.REVOKE, "invitations", 2),
            (AuditAction.UPDATE, "users", 18),
            (AuditAction.DELETE, "users", 2),
            (AuditAction.UPDATE, "tenants", 3),
        ]
    return LA_45_AUDIT_PLAN


def _audit_keys(profile) -> list[str]:
    keys: list[str] = []
    for action, entity, count in _audit_plan(profile):
        keys.extend(f"{action}:{entity}:{index}" for index in range(count))
    return keys


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------


def owned_ids(context):
    """Exactly the rows this fixture writes, so the guard can refuse any other."""
    profile = context.profile
    return {
        "tenants": {context.tenant_id},
        "locations": {
            context.uid("locations", code) for code, *_rest in _sedes(profile)
        },
        "users": {
            context.uid("users", email) for _name, email, *_rest in _people(profile)
        },
        "invitations": {
            context.uid("invitations", email)
            for _state, email, *_rest in _invitations(profile)
        },
        "audit_log": {context.uid("audit_log", key) for key in _audit_keys(profile)},
    }


def _upsert(model, row_id, **fields):
    """Write a row only when it differs, so a second run genuinely changes
    nothing -- including `updated_at`."""
    existing = model.objects.filter(id=row_id).first()
    if existing is None:
        return model.objects.create(id=row_id, **fields), True
    changed = [
        name
        for name, value in fields.items()
        if getattr(existing, name if not name.endswith("_id") else name) != value
    ]
    if not changed:
        return existing, False
    for name, value in fields.items():
        setattr(existing, name, value)
    existing.save(update_fields=[*changed, "updated_at"])
    return existing, True


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


def build(context):
    """Write the identity fixture, inside the pin the command already opened."""
    profile = context.profile
    network = _network(profile)
    now = timezone.now()

    # The tenant row is inserted **inside** the pin, where `tenants`' own
    # `USING (id = app_current_tenant())` policy is exactly what it satisfies.
    tenant, _written = _upsert(
        Tenant,
        context.tenant_id,
        name=network["name"],
        slug=context.slug,
        nit=network["nit"],
        status=TenantStatus.ACTIVE,
        settings={
            "tenant": {
                "legal_name": network["legal_name"],
                "timezone": "America/Bogota",
                "currency": "COP",
                "number_format": "es-CO",
            }
        },
    )
    context.wrote("tenants", 1)

    by_code = {}
    for code, name, city, address, phone, kind in _sedes(profile):
        location, _ = _upsert(
            Location,
            context.uid("locations", code),
            tenant_id=tenant.id,
            code=code,
            name=name,
            type=kind,
            address=address,
            city=city,
            phone=phone,
        )
        by_code[code] = location
    context.wrote("locations", len(by_code))

    people = {}
    for index, (name, email, role, code) in enumerate(_people(profile)):
        user_id = context.uid("users", email)
        person = User.objects.filter(id=user_id).first()
        if person is None:
            person = User(id=user_id)
        person.tenant_id = tenant.id
        person.email = email
        person.name = name
        person.role = role
        person.location = by_code.get(code) if code else None
        person.status = UserStatus.ACTIVE
        person.last_login = now - timedelta(hours=index + 1)
        if not person.password:
            person.set_password(DEMO_PASSWORD)
        person.save()
        people[email] = person
    context.wrote("users", len(people))

    owner = next(person for person in people.values() if person.role == Role.OWNER)

    written_invitations = 0
    for state, email, role, code, days in _invitations(profile):
        invitation_id = context.uid("invitations", email)
        invitation = Invitation.objects.filter(id=invitation_id).first()
        if invitation is None:
            invitation = Invitation(id=invitation_id)
        invitation.tenant_id = tenant.id
        invitation.email = email
        invitation.role = role
        invitation.location = by_code.get(code) if code else None
        invitation.invited_by = owner
        invitation.expires_at = now + timedelta(days=days)
        invitation.status = (
            InvitationStatus.REVOKED if state == "revoked" else InvitationStatus.PENDING
        )
        invitation.revoked_at = now - timedelta(days=1) if state == "revoked" else None
        invitation.accepted_at = None
        invitation.last_delivery_error = (
            "El servidor de correo respondió 550 después de 5 intentos."
            if state == "delivery_failed"
            else ""
        )
        invitation.token_hash = invitation_service.hash_token(
            invitation_service.token_for(invitation)
        )
        invitation.save()
        written_invitations += 1
        if invitation.status == InvitationStatus.PENDING:
            context.note(
                f"  invitación {state:<16} {email:<32} "
                f"{invitation.accept_url(invitation_service.token_for(invitation))}"
            )
    context.wrote("invitations", written_invitations)

    _build_audit(context, tenant, people, now)


def _build_audit(context, tenant, people, now):
    """The trail, with its newest rows inside the last twelve hours so the
    relative ladder and the absolute stamp both render (§B.9.1)."""
    actors = list(people.values())
    elevated = [
        person for person in actors if person.role in (Role.OWNER, Role.ADMIN)
    ] or actors
    keys = _audit_keys(context.profile)
    total = len(keys)
    written = 0
    for index, key in enumerate(keys):
        action, entity, _ordinal = key.split(":")
        actor = elevated[index % len(elevated)]
        # The three newest rows sit inside the last twelve hours; everything
        # older walks back a few hours at a time across the network's life.
        age = (
            timedelta(hours=2 * (total - index))
            if total - index <= 3
            else timedelta(days=(total - index) // 3, hours=(total - index) % 7)
        )
        before, after = _audit_payload(action, entity, tenant, actor)
        audit.record(
            actor=actor,
            tenant_id=tenant.id,
            action=action,
            entity_type=entity,
            entity_id=tenant.id if entity == "tenants" else actor.id,
            before=before,
            after=after,
            request_id=f"req_seed{index:04d}",
            row_id=context.uid("audit_log", key),
            at=now - age,
        )
        written += 1
    context.wrote("audit_log", written)


def _audit_payload(action, entity, tenant, actor):
    """`before` is null on a create and `after` is null on a delete."""
    if action == AuditAction.CREATE:
        return None, {"email": actor.email, "role": actor.role}
    if action == AuditAction.DELETE:
        return {"email": actor.email, "role": actor.role}, None
    if entity == "tenants":
        return (
            {"name": tenant.name, "timezone": "America/Bogota"},
            {"name": tenant.name, "timezone": "America/Bogota"},
        )
    if action == AuditAction.REVOKE:
        return {"status": "pending"}, {"status": "revoked"}
    if action == AuditAction.SEND:
        return {"email": actor.email}, {"email": actor.email, "resent": True}
    return {"role": Role.CASHIER}, {"role": actor.role}


register(
    "identity",
    tables=("tenants", "locations", "users", "invitations", "audit_log"),
    build=build,
    owned_ids=owned_ids,
)
