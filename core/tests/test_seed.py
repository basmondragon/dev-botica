"""The demo seed: five profiles, the guard, the registry, the counts."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.demo import identity, registry
from core.models import AuditLog, Invitation, Location, Tenant, User


def _seed(profile):
    call_command("seed_demo_tenant", profile=profile)
    return registry.uid(profile, "tenants", identity.slug_for(profile))


def _counts(tenant_id):
    """The suite runs as the migration role, which holds BYPASSRLS, so every
    count here states its tenant rather than leaning on the pin."""
    return (
        Location.objects.filter(tenant_id=tenant_id).count(),
        User.objects.filter(tenant_id=tenant_id).count(),
        Invitation.objects.filter(tenant_id=tenant_id).count(),
        AuditLog.objects.filter(tenant_id=tenant_id).count(),
    )


@pytest.mark.django_db
def test_a_bare_run_refuses_and_prints_the_five_profiles():
    with pytest.raises(CommandError) as refusal:
        call_command("seed_demo_tenant")
    for profile in registry.PROFILES:
        assert profile in str(refusal.value)


@pytest.mark.django_db
def test_an_unknown_profile_is_refused():
    with pytest.raises(CommandError):
        call_command("seed_demo_tenant", profile="invented")


@pytest.mark.django_db
def test_the_identity_fixture_builds_the_numbers_every_check_asserts_against():
    tenant_id = _seed("default")
    assert Tenant.objects.get(id=tenant_id).name == "Droguerías La 45"
    assert _counts(tenant_id) == (6, 9, 4, 43)


@pytest.mark.django_db
def test_the_identity_fixture_is_the_same_under_default_young_and_cold():
    """Those three profiles differ only in what the *later* stages build on top
    of it, so a check written against the numbers holds on all three."""
    for profile in ("default", "young", "cold"):
        assert _counts(_seed(profile)) == (6, 9, 4, 43)


@pytest.mark.django_db
def test_minimal_is_the_tenant_the_isolation_checks_are_isolated_from():
    tenant_id = _seed("minimal")
    assert Tenant.objects.get(id=tenant_id).name == "Farmacia La Estrella"
    assert _counts(tenant_id) == (1, 1, 0, 1)


@pytest.mark.django_db
def test_scale_carries_the_networks_one_warehouse():
    """The `location_type` enum's second value is exercised here rather than by
    turning one of the six drawn sedes into a bodega no later fixture can sell
    from."""
    from core.models import LocationType

    tenant_id = _seed("scale")
    sedes = Location.objects.filter(tenant_id=tenant_id)
    assert sedes.count() == 20
    assert sedes.filter(type=LocationType.WAREHOUSE).count() == 1
    assert User.objects.filter(tenant_id=tenant_id).count() == 24


@pytest.mark.django_db
def test_the_four_invitation_states_are_all_rendered():
    """A roster of only active people never renders three of the four badges,
    and an unrendered treatment is an unreviewed one."""
    tenant_id = _seed("default")
    states = {row.state for row in Invitation.objects.filter(tenant_id=tenant_id)}
    assert states == {"pending", "expired", "revoked", "delivery_failed"}


@pytest.mark.django_db
def test_every_audit_action_entity_pair_s0_writes_is_present():
    tenant_id = _seed("default")
    pairs = set(
        AuditLog.objects.filter(tenant_id=tenant_id)
        .values_list("action", "entity_type")
        .distinct()
    )
    assert pairs == {
        ("create", "invitations"),
        ("send", "invitations"),
        ("revoke", "invitations"),
        ("create", "users"),
        ("update", "users"),
        ("delete", "users"),
        ("update", "tenants"),
    }


@pytest.mark.django_db
def test_the_newest_rows_are_inside_the_last_twelve_hours():
    """So the relative ladder and the absolute stamp both render (§B.9.1)."""
    from datetime import timedelta

    from django.utils import timezone

    tenant_id = _seed("default")
    newest = list(
        AuditLog.objects.filter(tenant_id=tenant_id)
        .order_by("-created_at")
        .values_list("created_at", flat=True)[:3]
    )
    cutoff = timezone.now() - timedelta(hours=12)
    assert all(stamp > cutoff for stamp in newest)


@pytest.mark.django_db
def test_a_second_run_changes_nothing_and_every_id_is_unchanged():
    def _ids(tenant_id):
        return {
            model.__name__: set(
                model.objects.filter(tenant_id=tenant_id).values_list("id", flat=True)
            )
            for model in (Location, User, Invitation, AuditLog)
        }

    tenant_id = _seed("default")
    before = _ids(tenant_id)
    _seed("default")
    assert _ids(tenant_id) == before


@pytest.mark.django_db
def test_a_run_against_a_tenant_holding_a_foreign_row_writes_nothing():
    """The failure this guards against is not a broken demo but synthetic rows
    in a client's database."""
    import uuid

    tenant_id = _seed("default")
    Location.objects.create(
        id=uuid.uuid4(), tenant_id=tenant_id, code="ZZZ", name="Real"
    )
    with pytest.raises(CommandError) as refusal:
        call_command("seed_demo_tenant", profile="default")
    assert "locations" in str(refusal.value)


@pytest.mark.django_db
def test_a_fixture_writing_into_an_undeclared_table_fails_the_run():
    """The cheapest way to catch a stage that seeded around another stage's
    service rather than through it."""
    import uuid

    from core.models import Location as LocationModel

    def _rogue(context):
        LocationModel.objects.create(
            id=uuid.uuid4(),
            tenant_id=context.tenant_id,
            code="RGE",
            name="Undeclared",
        )

    registry.register(
        "rogue",
        tables=("audit_log",),
        requires=("identity",),
        build=_rogue,
        owned_ids=lambda context: {},
    )
    try:
        with pytest.raises(CommandError) as refusal:
            call_command("seed_demo_tenant", profile="minimal")
        assert "did not declare" in str(refusal.value)
    finally:
        registry.REGISTRY.pop("rogue", None)


@pytest.mark.django_db
def test_every_profile_answers_every_fixture():
    """A profile a fixture silently ignores is a profile whose screens nobody
    reviewed."""
    for profile in registry.PROFILES:
        context = registry.SeedContext(
            profile=profile,
            tenant_id=registry.uid(profile, "tenants", identity.slug_for(profile)),
            slug=identity.slug_for(profile),
        )
        owned = identity.owned_ids(context)
        assert set(owned) == {
            "tenants",
            "locations",
            "users",
            "invitations",
            "audit_log",
        }


@pytest.mark.django_db
def test_the_seed_touches_only_tenants_whose_slug_begins_demo():
    for profile in registry.PROFILES:
        assert identity.slug_for(profile).startswith("demo-")
