"""The sales-history loader, and the quarantine that makes it safe.

**An imported sale was issued by another system years ago.** It has no shift, no
device, no payment, no `stock_moves` row and no fiscal document -- and the check
constraint this stage migrated onto `sales` refuses the first two at the
database rather than in the loader. That is the difference between a quarantine
by construction and a quarantine by discipline, and it is the sentence in this
stage most likely to be violated later and hardest to find once it has been.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import Sum

from core.models import (
    Device,
    DeviceStatus,
    ImportRun,
    Sale,
    SaleLine,
    SaleSource,
    SaleStatus,
    StockMove,
)
from core.purchasing import loader as history
from core.tenancy import pin_tenant
from core.tests.test_sync_pull import make_item

pytestmark = pytest.mark.django_db

HEADER = ",".join(history.HEADER)


def write_file(tmp_path: Path, rows) -> Path:
    path = tmp_path / history.FILE
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def run(tenant, path, *, apply=True, **extra):
    with pin_tenant(tenant.id):
        return history.Loader(
            tenant_id=tenant.id, path=path, apply=apply, **extra
        ).run()


def fixture_rows(sede_code, item_name):
    """Two documents, four lines, across two months of a legacy system."""
    return [
        f"F-1001,{sede_code},2024-03-18T10:14:00,1001,{item_name},2,3900,0,2100,excluded",
        f"F-1001,{sede_code},2024-03-18T10:14:00,1001,{item_name},1,3900,0,2100,excluded",
        f"F-1002,{sede_code},2024-04-02T16:40:00,1002,{item_name},5,3900,500,2100,excluded",
        f"F-1003,{sede_code},2024-04-19T09:02:00,1003,{item_name},1,3900,0,,excluded",
    ]


def test_a_dry_run_reports_and_writes_nothing(tenant_a, sede_a, tmp_path):
    """A run is a dry run unless `--apply` is passed, and **the preview is on
    the record too**: the `imports` row is written either way."""
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    path = write_file(tmp_path, fixture_rows(sede_a.code, item.name))

    run_row = run(tenant_a, path, apply=False)
    with pin_tenant(tenant_a.id):
        assert Sale.objects.count() == 0
        assert SaleLine.objects.count() == 0
        assert ImportRun.objects.filter(kind=history.KIND, dry_run=True).count() == 1
    assert run_row.rows_read == 4


def test_the_loader_writes_closed_imported_sales_with_their_lines(
    tenant_a, sede_a, tmp_path
):
    """Acceptance 14 · an `imports` row and `sales` + `sale_lines` at
    `source = imported`. **A sale is written with its lines or not at all**: a
    `sales` row with no `sale_lines` teaches a per-item forecast nothing."""
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    path = write_file(tmp_path, fixture_rows(sede_a.code, item.name))
    run(tenant_a, path)

    with pin_tenant(tenant_a.id):
        sales = list(Sale.objects.all())
        assert len(sales) == 3
        assert {one.source for one in sales} == {SaleSource.IMPORTED}
        assert {one.status for one in sales} == {SaleStatus.CLOSED}
        assert all(one.shift_id is None and one.device_id is None for one in sales)
        assert SaleLine.objects.count() == 4
        assert all(one.number.startswith(history.NUMBER_PREFIX) for one in sales)
        first = Sale.objects.get(number=f"{history.NUMBER_PREFIX}1001")
        assert first.lines.count() == 2
        assert first.total == Decimal("11700.00")


def test_re_running_the_same_file_writes_nothing_new(tenant_a, sede_a, tmp_path):
    """Acceptance 14 · **re-running the same file writes nothing new**, and
    `imports` gains one row per run. Every identity is derived from the tenant,
    the source and the legacy document's own id, so the second run collides with
    `UNIQUE (tenant_id, client_uuid)` instead of doubling a history."""
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    path = write_file(tmp_path, fixture_rows(sede_a.code, item.name))
    run(tenant_a, path)
    with pin_tenant(tenant_a.id):
        before = (Sale.objects.count(), SaleLine.objects.count())

    second = run(tenant_a, path)
    with pin_tenant(tenant_a.id):
        assert (Sale.objects.count(), SaleLine.objects.count()) == before
        assert ImportRun.objects.filter(kind=history.KIND).count() == 2
    assert second.rows_failed == 0


def test_an_unmapped_location_is_refused_rather_than_guessed(
    tenant_a, sede_a, tmp_path
):
    """§11.6 · a legacy code no sede answers to is a row the loader refuses.
    **A placeholder sede invented to make a file load is debris somebody finds
    six months later on a screen that matters.**"""
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    path = write_file(tmp_path, fixture_rows("SEDE-07", item.name))
    run_row = run(tenant_a, path)
    assert run_row.rows_failed == 4
    assert Sale.objects.filter(tenant=tenant_a).count() == 0
    assert "--location" in run_row.errors[0]["reason"]


def test_a_location_mapping_places_the_rows(tenant_a, sede_a, tmp_path):
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    path = write_file(tmp_path, fixture_rows("SEDE-07", item.name))
    run_row = run(tenant_a, path, locations={"SEDE-07": sede_a.code})
    assert run_row.rows_failed == 0
    with pin_tenant(tenant_a.id):
        assert Sale.objects.filter(location=sede_a).count() == 3


def test_an_unknown_reference_is_refused_rather_than_created(
    tenant_a, sede_a, tmp_path
):
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    path = write_file(tmp_path, fixture_rows(sede_a.code, "Producto que no existe"))
    run_row = run(tenant_a, path)
    assert run_row.rows_failed == 4
    with pin_tenant(tenant_a.id):
        assert Sale.objects.count() == 0
    del item


def test_the_database_refuses_an_imported_sale_inside_a_shift(
    tenant_a, sede_a, tmp_path
):
    """Acceptance 15 · **an imported sale with a `shift_id` or a `device_id` is
    refused by the database, not by the loader.**"""
    import uuid

    from core.models import Shift, ShiftStatus

    with pin_tenant(tenant_a.id):
        shift = Shift.objects.create(
            tenant_id=tenant_a.id,
            location=sede_a,
            status=ShiftStatus.OPEN,
            client_uuid=uuid.uuid4(),
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            Sale.objects.create(
                tenant_id=tenant_a.id,
                location=sede_a,
                shift=shift,
                number="HIST-9",
                status=SaleStatus.CLOSED,
                source=SaleSource.IMPORTED,
                client_uuid=uuid.uuid4(),
            )
    del tmp_path


def test_an_import_reaches_no_stock_move_and_no_shift(tenant_a, sede_a, tmp_path):
    """Acceptance 15 · no imported sale appears in `stock_moves`, in any shift,
    or in any cash reconciliation."""
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    path = write_file(tmp_path, fixture_rows(sede_a.code, item.name))
    run(tenant_a, path)
    with pin_tenant(tenant_a.id):
        imported = Sale.objects.filter(source=SaleSource.IMPORTED)
        assert (
            StockMove.objects.filter(
                document_id__in=imported.values_list("id", flat=True)
            ).count()
            == 0
        )
        assert imported.filter(shift__isnull=False).count() == 0
        assert imported.aggregate(total=Sum("total"))["total"] > 0


def test_a_device_named_like_the_prefix_refuses_the_whole_run(
    tenant_a, sede_a, tmp_path
):
    """**The prefix is what makes an imported number uncollidable**, and the one
    way it could collide is refused before a row is read -- weeks before it
    would otherwise fail at a counter with a customer waiting."""
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    with pin_tenant(tenant_a.id):
        Device.objects.create(
            tenant_id=tenant_a.id,
            location=sede_a,
            code="HIST",
            label="Caja 1",
            status=DeviceStatus.ACTIVE,
            device_key_hash="x" * 64,
        )
    path = write_file(tmp_path, fixture_rows(sede_a.code, item.name))
    run_row = run(tenant_a, path)
    assert run_row.rows_failed == 1
    with pin_tenant(tenant_a.id):
        assert Sale.objects.count() == 0


def test_the_command_refuses_without_a_tenant(tmp_path):
    """Rule 6, context two · **the tenant is an explicit required argument and
    the command pins before doing any work.** It must refuse at the argument
    parser, before it opens a connection."""
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("load_sales_history", "--file", str(tmp_path / "x.csv"))


def test_the_command_writes_only_into_the_tenant_it_was_given(
    tenant_a, tenant_b, sede_a, tmp_path
):
    """The check whose absence costs the most, because its failure makes no
    noise: under `FORCE ROW LEVEL SECURITY` an unpinned run writes **zero rows**
    and exits 0. **Assert on rows written, never on exit status.**"""
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    path = write_file(tmp_path, fixture_rows(sede_a.code, item.name))
    call_command(
        "load_sales_history", "--tenant", tenant_a.slug, "--file", str(path), "--apply"
    )
    # **Asserted on rows written, and per tenant explicitly.** The suite runs as
    # the migration role, which holds BYPASSRLS, so a pin proves nothing here --
    # what proves the command wrote where it was told is counting each network's
    # own rows.
    assert Sale.objects.filter(tenant=tenant_a, source=SaleSource.IMPORTED).count() == 3
    assert Sale.objects.filter(tenant=tenant_b).count() == 0


def test_a_malformed_header_fails_the_file_and_not_the_row(tenant_a, tmp_path):
    path = tmp_path / history.FILE
    path.write_text("external_id,location\nF-1,CHA\n", encoding="utf-8")
    run_row = run(tenant_a, path)
    assert run_row.rows_failed == 1
    assert "columnas" in run_row.errors[0]["reason"]


def test_a_repeated_legacy_number_fails_its_own_document(tenant_a, sede_a, tmp_path):
    """A legacy system that reused a document number at one sede refuses that
    document and nothing else. **A run that failed nine thousand rows because
    the ninth was broken is as wrong as one that loads eight thousand and exits
    zero, and both are quiet.**"""
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    rows = [
        f"F-1,{sede_a.code},2024-03-18T10:14:00,7001,{item.name},2,3900,0,2100,excluded",
        f"F-2,{sede_a.code},2024-03-19T10:14:00,7001,{item.name},1,3900,0,2100,excluded",
        f"F-3,{sede_a.code},2024-03-20T10:14:00,7003,{item.name},1,3900,0,2100,excluded",
    ]
    path = write_file(tmp_path, rows)
    run_row = run(tenant_a, path)

    with pin_tenant(tenant_a.id):
        assert Sale.objects.count() == 2
        assert SaleLine.objects.count() == 2
    assert run_row.rows_failed == 1
    assert "repetido" in run_row.errors[0]["reason"]


def test_an_imported_sale_never_reaches_a_till(tenant_a, sede_a, tmp_path):
    """S2's registry, and this is the regression this stage could have caused.

    A legacy export that runs right up to cutover carries last week's sales,
    which fall inside the till's own seven-day retention window. **The
    collection's membership predicate names `source`**, so they replicate to
    nobody: a ticket list showing sales no cashier rang is the counter reading
    two systems at once.
    """
    from datetime import timedelta

    from django.utils import timezone

    from core.sync import registry, settings as sync_settings

    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    yesterday = (timezone.now() - timedelta(days=1)).isoformat(timespec="seconds")
    path = write_file(
        tmp_path,
        [
            f"F-9,{sede_a.code},{yesterday},9001,{item.name},1,3900,0,2100,excluded",
        ],
    )
    run(tenant_a, path)

    with pin_tenant(tenant_a.id):
        options = sync_settings.DEFAULTS
        collection = next(one for one in registry.COLLECTIONS if one.name == "sales")
        members = collection.base(tenant_a.id, sede_a.id, options).filter(
            collection.member_q(options)
        )
        assert Sale.objects.filter(source=SaleSource.IMPORTED).count() == 1
        assert members.count() == 0


def test_the_office_ventas_list_shows_the_counters_own_sales(
    client_as, owner_a, tenant_a, sede_a, tmp_path
):
    """**Every rollup, KPI, cash, shift and fiscal query filters on
    `sales.source`, and the default is `counter`** (*Data*). An owner opening
    Ventas after an import must not find two years of another system's turnover
    mixed into the period they asked for.
    """
    item = make_item(tenant_a, "Suero oral 500 ml", tracks_lots=False)
    path = write_file(tmp_path, fixture_rows(sede_a.code, item.name))
    run(tenant_a, path)

    listed = client_as(owner_a).get("/api/sales").json()
    assert listed["row_count"] == 0

    named = client_as(owner_a).get("/api/sales?source=imported").json()
    assert named["row_count"] == 3
