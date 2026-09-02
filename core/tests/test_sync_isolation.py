"""What S2's two tables owe A1, and what S2's indexes owe S1.

The regression surface is larger than this stage's own schema suggests: S2
touches six of S1's tables and three of S0's paths without owning any of them.
"""

import re

import pytest
from django.db import connection
from django.utils import timezone

from core.models import Customer, Device, SyncConflict, SyncConflictType
from core.sync import conflicts as conflict_service
from core.tenancy import pin_tenant
from core.tests.conftest import make_location
from core.tests.test_sync_pull import make_device, make_item

pytestmark = pytest.mark.django_db


NEW_TABLES = ("devices", "sync_conflicts")


def test_both_new_tables_report_row_security_enabled_and_forced():
    """Criterion 24 · row security **on but not forced** is a table the runtime
    role reads across tenants, and it is invisible in every other check."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity "
            "FROM pg_class WHERE relname = ANY(%s)",
            [list(NEW_TABLES)],
        )
        rows = {name: (enabled, forced) for name, enabled, forced in cursor.fetchall()}
    assert rows == {table: (True, True) for table in NEW_TABLES}


def test_a_query_outside_a_pin_returns_zero_rows(tenant_a, sede_a, as_runtime_role):
    """Criterion 24's second half. An unpinned connection reads and writes zero
    rows -- not another tenant's."""
    device, _key = make_device(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        conflict_service.raise_conflict(
            device=device, type=SyncConflictType.DEVICE_SILENT, detail={}
        )
    as_runtime_role()
    assert Device.objects.count() == 0
    assert SyncConflict.objects.count() == 0


def test_one_networks_device_is_invisible_from_another(
    tenant_a, tenant_b, sede_a, as_runtime_role
):
    other = make_location(tenant_b, "EST", "La Estrella")
    device_a, _ = make_device(tenant_a, sede_a)
    device_b, _ = make_device(tenant_b, other)

    with pin_tenant(tenant_a.id):
        as_runtime_role()
        assert list(Device.objects.values_list("id", flat=True)) == [device_a.id]


def test_a_device_key_hash_from_another_network_finds_nothing(
    tenant_a, tenant_b, sede_a, as_runtime_role
):
    """The credential lookup is a unique index on a hash, which is exactly the
    shape that would leak across tenants without `FORCE ROW LEVEL SECURITY`."""
    other = make_location(tenant_b, "EST", "La Estrella")
    _device_b, key_b = make_device(tenant_b, other)
    from core.sync import devices as device_service

    with pin_tenant(tenant_a.id):
        as_runtime_role()
        assert (
            Device.objects.filter(
                device_key_hash=device_service.hash_key(key_b)
            ).first()
            is None
        )


# ---------------------------------------------------------------------------
# What this stage would break
# ---------------------------------------------------------------------------


def test_the_customers_natural_key_index_is_S1s_and_is_still_partial(tenant_a):
    """Rule 4 · an index is created once by the first stage that needs it. S2's
    push declares `(tenant_id, document_type, document)` as its idempotency key
    and inherits S1's index rather than migrating a duplicate.

    It stays partial on `document <> ''` because S1's Ley 1581 erasure clears
    the identifying fields in place, and a total index would refuse the second
    erased row.
    """
    Customer.objects.create(tenant=tenant_a, name="Sin documento")
    Customer.objects.create(tenant=tenant_a, name="Tampoco")
    assert Customer.objects.filter(tenant=tenant_a, document="").count() == 2

    Customer.objects.create(
        tenant=tenant_a, document_type="CC", document="123", name="Ana"
    )
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        Customer.objects.create(
            tenant=tenant_a, document_type="CC", document="123", name="Ana otra vez"
        )


def test_the_six_delta_indexes_exist_under_their_declared_names():
    """A collection declared without an index in one of the two shapes fails the
    build, and this is where that is checked against the database rather than
    against the model state."""
    expected = {
        "items_delta_cursor",
        "item_barcodes_delta_cursor",
        "manufacturers_delta_cursor",
        "categories_delta_cursor",
        "customers_delta_cursor",
        "item_prices_delta_cursor",
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE indexname = ANY(%s)",
            [sorted(expected)],
        )
        found = {row[0] for row in cursor.fetchall()}
    assert found == expected


def test_the_delta_index_serves_the_pull_with_no_sort_step(tenant_a, sede_a):
    """§4 · `/api/sync/pull` is a single indexed tuple scan.

    The load-bearing property is not *which* plan Postgres picks on a fifty-row
    test table -- it will read one page sequentially and be right to -- but that
    the index **can** serve the whole query, ordering included. A `Sort` node in
    this plan means the index's columns are in the wrong order, and at four
    thousand items that is the difference between the 20 ms budget and a scan of
    the catalog on every till every eight seconds.
    """
    for index in range(50):
        make_item(tenant_a, f"Producto {index:03d}")
    cursor_time = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE items")
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            "EXPLAIN SELECT id, updated_at FROM items "
            "WHERE tenant_id = %s AND (updated_at, id) > (%s, %s) "
            "AND updated_at <= %s ORDER BY updated_at, id LIMIT 500",
            [
                str(tenant_a.id),
                cursor_time,
                "00000000-0000-0000-0000-000000000000",
                cursor_time,
            ],
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())
    assert "items_delta_cursor" in plan, plan
    assert "Sort" not in plan, plan


def test_no_module_outside_core_sync_issues_a_cursor_query():
    """Criterion 30's server half · **the boundary §5 requires, checked as a
    boundary rather than as a convention.**

    All sync code lives behind `core/sync/` so that if §4's measurements ever
    justify an engine — PowerSync, ElectricSQL, logical replication — it
    replaces that package and nothing else moves. A convention nobody can fail
    is a convention that quietly stops being true around the fourth stage.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in sorted(root.glob("core/**/*.py")) + sorted(root.glob("botica/*.py")):
        relative = str(path.relative_to(root))
        if relative.startswith(("core/sync/", "core/tests/", "core/migrations/")):
            continue
        source = path.read_text()
        # Comments are prose, not code; this module's own docstrings discuss the
        # cursor at length and would report themselves.
        code = re.sub(r"(?m)#.*$", "", source)
        # Reaching into the protocol's own modules.
        if re.search(r"core\.sync\.(pull|push|registry|digest)", code):
            offenders.append(relative)
        # Or writing the cursor comparison by hand, wherever it is.
        if "updated_at__gt" in code:
            offenders.append(relative)
    # `core/api.py` mounts the router and `core/tasks.py` registers the job;
    # both name the package and neither reaches into the protocol.
    assert offenders == []

    # The delta indexes are *declared* on S1's models under rule 4, because the
    # model state and the migration state have to agree — that is a declaration
    # and not a query, and `core/models.py` is the only place it belongs.
    declaring = [
        str(path.relative_to(root))
        for path in sorted(root.glob("core/**/*.py"))
        if "delta_cursor" in path.read_text()
        and not str(path.relative_to(root)).startswith(
            ("core/migrations/", "core/tests/")
        )
    ]
    assert declaring == ["core/models.py"]


def test_the_barcode_page_scan_carries_no_join(tenant_a, sede_a):
    """§4 · **anything that makes the pull a join is a defect**, and
    `item_barcodes` is the one collection whose membership rule reads another
    table.

    It resolves that in a second indexed lookup over the page rather than in the
    scan the 20 ms budget is measured on -- so the query that runs every eight
    seconds on every till names one table, and a page that returns zero rows
    costs zero lookups.
    """
    from core.sync import registry

    collection = registry.BARCODES
    assert not any("__" in field for field in collection.fields), (
        "a `values()` field spanning a relation puts a join in the cursor query"
    )

    query = str(
        collection.base(tenant_a.id, sede_a.id, {})
        .order_by("updated_at", "id")
        .values(*collection.fields)
        .query
    )
    assert "items" not in query.lower(), query
    assert " JOIN " not in query.upper(), query
