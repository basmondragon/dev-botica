"""The tills the seed builds, and the one job S2 ships.

The seed is the last check rather than the first: a stage whose screens do not
render convincingly from it is not finished, and a fixture where every device is
healthy never exercises the question support is actually asked.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.demo import registry as seed_registry
from core.models import Device, DeviceStatus, SyncConflict, SyncConflictType
from core.sync import demo as device_fixture
from core.sync import jobs
from core.tenancy import pin_tenant

pytestmark = pytest.mark.django_db


def build(profile):
    slug = __import__("core.demo.identity", fromlist=["identity"]).slug_for(profile)
    tenant_id = seed_registry.uid(profile, "tenants", slug)
    context = seed_registry.SeedContext(profile=profile, tenant_id=tenant_id, slug=slug)
    with pin_tenant(tenant_id):
        seed_registry.run_profile(context)
    return context


@pytest.mark.parametrize(
    "profile,expected", [("default", 7), ("minimal", 1), ("scale", 40)]
)
def test_every_profile_lands_the_fleet_its_stage_document_names(profile, expected):
    """`default` seven, `minimal` one, `scale` forty -- and `scale`'s forty is
    exactly the fleet *The poll schedule* does its arithmetic on, which turns
    ~30 pull requests per second from a figure taken on trust into one somebody
    can measure."""
    context = build(profile)
    assert context.written["devices"] == expected
    with pin_tenant(context.tenant_id):
        assert Device.objects.count() == expected


def test_chapinero_carries_two_tills_and_every_other_sede_one():
    """Criterion 9's first half, and the seed's own derivation rule."""
    context = build("default")
    with pin_tenant(context.tenant_id):
        by_sede = {}
        for device in Device.objects.select_related("location"):
            by_sede.setdefault(device.location.code, []).append(device.label)
    assert sorted(by_sede["CHA"]) == ["Caja 1", "Caja 2"]
    assert all(len(labels) == 1 for code, labels in by_sede.items() if code != "CHA")


def test_exactly_one_seeded_device_is_silent_and_none_is_ahead_of_the_server():
    """A list where every device is healthy never exercises the question support
    is actually asked. And a `last_synced_at` in the future renders `hace -3 s`,
    which is how a stranger finds the fixture."""
    context = build("default")
    now = timezone.now()
    stale = now - timedelta(hours=48)
    with pin_tenant(context.tenant_id):
        rows = list(Device.objects.all())
    silent = [row for row in rows if row.last_synced_at < stale]
    assert len(silent) == 1
    assert all(row.last_synced_at <= now for row in rows)
    assert all(row.last_seen_at <= now for row in rows)


def test_minimals_only_device_is_not_the_silent_one():
    """A fixture whose only device is stale fails every S0 check that needs a
    healthy till, for a reason S0 did not cause."""
    context = build("minimal")
    with pin_tenant(context.tenant_id):
        device = Device.objects.get()
    assert device.last_synced_at > timezone.now() - timedelta(minutes=1)


def test_a_second_run_writes_the_same_rows(monkeypatch):
    """The guard's own property: a rerun over the seed's own rows is idempotent,
    so a demo can be rebuilt without a fresh database."""
    first = build("default")
    with pin_tenant(first.tenant_id):
        before = sorted(str(one) for one in Device.objects.values_list("id", flat=True))
    second = build("default")
    with pin_tenant(second.tenant_id):
        after = sorted(str(one) for one in Device.objects.values_list("id", flat=True))
    assert before == after


def test_the_seed_derives_its_device_ids_so_a_saved_link_still_points_at_one():
    context = build("default")
    expected = device_fixture.owned_ids(context)["devices"]
    with pin_tenant(context.tenant_id):
        assert set(Device.objects.values_list("id", flat=True)) == expected


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


def test_a_silent_device_raises_exactly_one_conflict_per_day():
    """Criterion 19 · running the job twice on the same day produces no second
    row. The idempotency key is `(tenant_id, device_id, date)`."""
    context = build("default")
    day = timezone.localdate()

    raised = jobs.stale_device_check.func(
        tenant_id=str(context.tenant_id), run_date=day.isoformat()
    )
    assert raised == 1

    again = jobs.stale_device_check.func(
        tenant_id=str(context.tenant_id), run_date=day.isoformat()
    )
    assert again == 1

    with pin_tenant(context.tenant_id):
        rows = list(SyncConflict.objects.filter(type=SyncConflictType.DEVICE_SILENT))
    assert len(rows) == 1
    assert rows[0].detail["reason"] == "device_silent"
    assert rows[0].detail["hours"] == 48


def test_the_job_never_revokes_anything():
    """A till that is quiet because the shop was closed is not a till to
    disable, and a job that disabled one would be a Monday morning with no
    counter."""
    context = build("default")
    jobs.stale_device_check.func(
        tenant_id=str(context.tenant_id), run_date=timezone.localdate().isoformat()
    )
    with pin_tenant(context.tenant_id):
        assert Device.objects.filter(status=DeviceStatus.REVOKED).count() == 0


def test_a_new_day_raises_a_new_row():
    """`per day` is literal: yesterday's row stands and today's is its own."""
    context = build("default")
    today = timezone.localdate()
    jobs.stale_device_check.func(
        tenant_id=str(context.tenant_id),
        run_date=(today - timedelta(days=1)).isoformat(),
    )
    jobs.stale_device_check.func(
        tenant_id=str(context.tenant_id), run_date=today.isoformat()
    )
    with pin_tenant(context.tenant_id):
        assert (
            SyncConflict.objects.filter(type=SyncConflictType.DEVICE_SILENT).count()
            == 2
        )
