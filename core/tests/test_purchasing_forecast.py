"""The v1 forecast, regime by regime, on a tenant built by hand.

**The parametric regime is checked first, because it is day one.** §1 makes a
client's sales history an accelerant and never a precondition, so the first
thing that has to be true of this stage is that a droguería with no sales at all
still gets an order -- and that every line of it says, in words, that it came
from the sede's own parameters rather than from a model that has seen anything.

The rest is the arithmetic stated in the stage document: what censors a week,
what promotes an item, what demotes one, what a `parametric` row is forbidden to
store, and what the refresh may and may not write back into `stock_policies`.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.inventory import ledger
from core.models import (
    DemandForecast,
    ForecastBasis,
    Item,
    PolicySource,
    Sale,
    SaleLine,
    SaleSource,
    SaleStatus,
    Shift,
    ShiftStatus,
    StockMoveType,
    StockPolicy,
    Supplier,
    SupplierItem,
    Tenant,
    VatClass,
)
from core.purchasing import forecast, orders as order_service
from core.purchasing import settings as purchasing_settings
from core.tenancy import pin_tenant
from core.tests.test_inventory_ledger import make_lot
from core.tests.test_sync_pull import make_item

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The harness. Everything here goes through the product's own services: stock
# moves through S3's ledger, never a hand-written `stock_on_hand` row.
# ---------------------------------------------------------------------------


def supplier(tenant, name="Coopidrogas"):
    ordinal = Supplier.objects.filter(tenant=tenant).count()
    return Supplier.objects.create(
        tenant=tenant, name=name, nit=f"999.000.{100 + ordinal}-1"
    )


def link(tenant, item, seller, *, cost="1000.00", min_pack=1, preferred=True):
    return SupplierItem.objects.create(
        tenant=tenant,
        supplier=seller,
        item=item,
        cost=Decimal(cost),
        min_order_pack=min_pack,
        is_preferred=preferred,
    )


def stock(tenant, location, item, quantity, *, lot=None, days_back=200):
    """Put units on a shelf **through the ledger**, dated far enough back that
    the censoring replay sees the shelf as stocked for the whole window."""
    with pin_tenant(tenant.id):
        ledger.append(
            [
                ledger.Move(
                    location_id=location.id,
                    item_id=item.id,
                    lot_id=lot.id if lot else None,
                    quantity=quantity,
                    type=StockMoveType.ADJUSTMENT,
                    reason="opening_stock",
                    recorded_at=timezone.now() - timedelta(days=days_back),
                    occurred_at=timezone.now() - timedelta(days=days_back),
                    key=f"open:{location.id}:{item.id}:{days_back}",
                )
            ],
            tenant_id=tenant.id,
        )


def sell(tenant, location, item, *, weeks_back, units, ordinal=0, imported=False):
    """One closed sale of one reference, `weeks_back` weeks ago.

    An imported row carries **no shift and no device**, which is what the check
    constraint this stage migrated onto `sales` refuses anything else.
    """
    when = timezone.now() - timedelta(weeks=weeks_back, days=1)
    shift = None
    if not imported:
        shift = Shift.objects.create(
            tenant=tenant,
            location=location,
            opened_at=when,
            closed_at=when + timedelta(hours=8),
            status=ShiftStatus.CLOSED,
            declared_total=Decimal("0"),
            variance=Decimal("0"),
            client_uuid=_key(f"shift:{location.id}:{item.id}:{weeks_back}:{ordinal}"),
        )
    sale = Sale.objects.create(
        tenant=tenant,
        location=location,
        shift=shift,
        number=f"{'HIST-' if imported else 'T1-'}{weeks_back}{ordinal}{item.id.hex[:4]}",
        status=SaleStatus.CLOSED,
        source=SaleSource.IMPORTED if imported else SaleSource.COUNTER,
        subtotal=Decimal("1000") * units,
        total=Decimal("1000") * units,
        occurred_at=when,
        recorded_at=when,
        closed_at=when,
        client_uuid=_key(f"sale:{item.id}:{weeks_back}:{ordinal}:{imported}"),
    )
    SaleLine.objects.create(
        tenant=tenant,
        sale=sale,
        location=location,
        position=1,
        item=item,
        quantity=units,
        unit_price=Decimal("1000"),
        vat_class=VatClass.EXCLUDED,
        occurred_at=when,
        recorded_at=when,
        client_uuid=_key(f"line:{item.id}:{weeks_back}:{ordinal}:{imported}"),
    )
    return sale


def _key(text):
    import uuid

    return uuid.uuid5(uuid.NAMESPACE_URL, text)


def policy(tenant, location, item, **fields):
    return StockPolicy.objects.create(
        tenant=tenant,
        item=item,
        location=location,
        source=PolicySource.MANUAL,
        **fields,
    )


def refresh(tenant, location):
    with pin_tenant(tenant.id):
        return forecast.refresh(tenant.id, location.id)


def row(tenant, location, item):
    return DemandForecast.objects.get(tenant=tenant, location=location, item=item)


# ---------------------------------------------------------------------------
# Day one: the parametric regime (acceptance 25, 26, 29)
# ---------------------------------------------------------------------------


def test_a_tenant_with_no_sales_still_gets_a_forecast_and_an_order(tenant_a, sede_a):
    """Acceptance 25 · **on a tenant with no sales at all and `stock_policies`
    rows at one sede, Compras on day one still produces an order.**

    Every line's basis is `parametric`, every band is Baja, and no line claims a
    season, a trend or a rotation.
    """
    item = make_item(tenant_a, "Losartán 50 mg × 30", tracks_lots=False)
    lot = None
    stock(tenant_a, sede_a, item, 4, lot=lot)
    policy(tenant_a, sede_a, item, reorder_point=10, max_quantity=60)
    link(tenant_a, item, supplier(tenant_a))

    refresh(tenant_a, sede_a)
    forecast_row = row(tenant_a, sede_a, item)

    assert forecast_row.basis == ForecastBasis.PARAMETRIC
    # §B.9.2 tier 3 · a row with no demand estimate **says so** rather than
    # storing a zero, and the database refuses anything else.
    assert forecast_row.weekly_sales is None
    assert forecast_row.trend is None
    assert forecast_row.coverage_days is None
    assert forecast_row.confidence == forecast.PARAMETRIC_POLICY_CONFIDENCE
    assert forecast.band(forecast_row.confidence) == "baja"

    with pin_tenant(tenant_a.id):
        built = order_service.generate(tenant_a.id, sede_a.id)
    assert len(built) == 1
    line = built[0].lines.get()
    assert line.basis == ForecastBasis.PARAMETRIC
    assert line.reason_code == "parametric_policy"
    # `max(0, max_quantity - on_hand - on_order)` where a maximum stands.
    assert line.suggested_quantity == 56
    assert line.approved_quantity == line.suggested_quantity


def test_no_history_and_no_policy_puts_a_reference_on_no_order(tenant_a, sede_a):
    """Acceptance 26 · **the model withholds.** No policy row and no category to
    assume from, so the reference is absent from the order entirely -- a screen
    full of invented quantities is worse than one that says what it is
    missing."""
    item = make_item(tenant_a, "Dermocosmético raro", tracks_lots=False)
    stock(tenant_a, sede_a, item, 5)
    link(tenant_a, item, supplier(tenant_a))

    refresh(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        built = order_service.generate(tenant_a.id, sede_a.id)
    assert built == []


def test_a_parametric_refresh_writes_no_model_policy(tenant_a, sede_a):
    """Acceptance 29 · **a refresh over a tenant with no sales writes no
    `stock_policies` row.** Every policy row is still `manual` afterwards and
    its `updated_at` has not moved."""
    item = make_item(tenant_a, "Metformina 850 mg × 30", tracks_lots=False)
    stock(tenant_a, sede_a, item, 30)
    standing = policy(tenant_a, sede_a, item, reorder_point=10, max_quantity=60)
    before = StockPolicy.objects.get(id=standing.id).updated_at

    refresh(tenant_a, sede_a)
    refresh(tenant_a, sede_a)

    assert StockPolicy.objects.filter(source=PolicySource.MODEL).count() == 0
    after = StockPolicy.objects.get(id=standing.id)
    assert after.updated_at == before
    assert after.reorder_point == 10


# ---------------------------------------------------------------------------
# Learning and learned (acceptance 17, 18, 27)
# ---------------------------------------------------------------------------


def test_two_weeks_of_sales_read_learning_and_never_render_alta(tenant_a, sede_a):
    """Acceptance 17 · an item with two weeks of its own sales reads `learning`,
    never renders **Alta**, and is floored by the sede's own reorder point."""
    item = make_item(tenant_a, "Ibuprofeno 400 mg × 50", tracks_lots=False)
    stock(tenant_a, sede_a, item, 12, days_back=20)
    policy(tenant_a, sede_a, item, reorder_point=40, max_quantity=200)
    link(tenant_a, item, supplier(tenant_a))
    for week in (1, 2):
        sell(tenant_a, sede_a, item, weeks_back=week, units=3)

    refresh(tenant_a, sede_a)
    fresh = row(tenant_a, sede_a, item)
    assert fresh.basis == ForecastBasis.LEARNING
    assert fresh.confidence <= forecast.LEARNING_CONFIDENCE_CAP
    assert forecast.band(fresh.confidence) != "alta"

    with pin_tenant(tenant_a.id):
        built = order_service.generate(tenant_a.id, sede_a.id)
    line = built[0].lines.get()
    # **The parametric floor.** A fortnight of quiet weeks cannot talk the model
    # out of restocking something the sede's own policy says to keep.
    assert line.reason_code == "learning_floor"
    assert line.suggested_quantity == 188


def test_a_fast_mover_with_a_long_history_reaches_learned(tenant_a, sede_a):
    """Promotion is a measurement, not a calendar: enough usable weeks **and** a
    relative standard error inside the threshold."""
    item = make_item(tenant_a, "Acetaminofén 500 mg × 100", tracks_lots=False)
    stock(tenant_a, sede_a, item, 300, days_back=210)
    link(tenant_a, item, supplier(tenant_a))
    for week in range(1, 21):
        sell(tenant_a, sede_a, item, weeks_back=week, units=40)

    refresh(tenant_a, sede_a)
    fresh = row(tenant_a, sede_a, item)
    assert fresh.basis == ForecastBasis.LEARNED
    assert fresh.weekly_sales is not None and fresh.weekly_sales > 0
    assert fresh.coverage_days is not None
    assert forecast.band(fresh.confidence) == "alta"


def test_two_regimes_on_one_screen_for_one_sede(tenant_a, sede_a):
    """Acceptance 27 · **two regimes on the same screen for the same sede on the
    same morning**, which is the per-item threshold working rather than a
    tenant-wide switch."""
    fast = make_item(tenant_a, "Dipirona 500 mg × 10", tracks_lots=False)
    slow = make_item(tenant_a, "Crema dermatológica 100 g", tracks_lots=False)
    stock(tenant_a, sede_a, fast, 200, days_back=210)
    stock(tenant_a, sede_a, slow, 20, days_back=210)
    for week in range(1, 21):
        sell(tenant_a, sede_a, fast, weeks_back=week, units=35)
    sell(tenant_a, sede_a, slow, weeks_back=9, units=1)

    refresh(tenant_a, sede_a)
    assert row(tenant_a, sede_a, fast).basis == ForecastBasis.LEARNED
    assert row(tenant_a, sede_a, slow).basis == ForecastBasis.LEARNING


def test_a_week_the_shelf_stood_empty_is_censored_not_counted_as_zero(tenant_a, sede_a):
    """**Selling nothing while having nothing is not demand information.**

    Two items sell the same units in the same weeks. One had stock throughout;
    the other's shelf was empty for the rest of the window. The censored one
    must not read as the lower seller of the two.
    """
    held = make_item(tenant_a, "Con existencias", tracks_lots=False)
    empty = make_item(tenant_a, "En quiebre", tracks_lots=False)
    stock(tenant_a, sede_a, held, 500, days_back=210)
    # The second shelf is only stocked five weeks ago, so every week before that
    # replays at or below zero and is dropped.
    stock(tenant_a, sede_a, empty, 500, days_back=35)
    for week in range(1, 5):
        sell(tenant_a, sede_a, held, weeks_back=week, units=20)
        sell(tenant_a, sede_a, empty, weeks_back=week, units=20)

    refresh(tenant_a, sede_a)
    assert (
        row(tenant_a, sede_a, empty).weekly_sales
        > row(tenant_a, sede_a, held).weekly_sales
    )


def test_an_item_with_no_sales_reads_a_dash_rather_than_a_zero(tenant_a, sede_a):
    """Acceptance 18 · never `0` and never `∞`: a row the model has no estimate
    for stores a null and the cell renders an em dash with its reason."""
    item = make_item(tenant_a, "Sin rotación", tracks_lots=False)
    stock(tenant_a, sede_a, item, 60, days_back=210)
    policy(tenant_a, sede_a, item, reorder_point=5)

    refresh(tenant_a, sede_a)
    fresh = row(tenant_a, sede_a, item)
    assert fresh.coverage_days is None
    assert fresh.basis == ForecastBasis.PARAMETRIC


# ---------------------------------------------------------------------------
# What the refresh writes back (acceptance 13)
# ---------------------------------------------------------------------------


def test_a_pharmacists_threshold_survives_every_refresh(tenant_a, sede_a):
    """Acceptance 13 · **this is the check most worth running every session,
    because its failure is silent and expensive.**"""
    item = make_item(tenant_a, "Losartán 50 mg × 30", tracks_lots=False)
    stock(tenant_a, sede_a, item, 300, days_back=210)
    standing = policy(tenant_a, sede_a, item, reorder_point=77, max_quantity=400)
    for week in range(1, 21):
        sell(tenant_a, sede_a, item, weeks_back=week, units=30)

    before = StockPolicy.objects.get(id=standing.id)
    refresh(tenant_a, sede_a)
    refresh(tenant_a, sede_a)
    after = StockPolicy.objects.get(id=standing.id)

    assert after.reorder_point == before.reorder_point == 77
    assert after.target_coverage_days == before.target_coverage_days
    assert after.source == PolicySource.MANUAL
    assert after.updated_at == before.updated_at
    assert StockPolicy.objects.filter(source=PolicySource.MODEL).count() == 0


def test_a_measured_item_with_no_manual_row_gets_a_model_policy(tenant_a, sede_a):
    """The other half: where nobody set a threshold, the model writes one, and
    it is marked `model` so `source` keeps meaning what it means."""
    item = make_item(tenant_a, "Naproxeno 500 mg × 20", tracks_lots=False)
    stock(tenant_a, sede_a, item, 300, days_back=210)
    for week in range(1, 21):
        sell(tenant_a, sede_a, item, weeks_back=week, units=25)

    refresh(tenant_a, sede_a)
    written = StockPolicy.objects.get(item=item, location=sede_a)
    assert written.source == PolicySource.MODEL
    assert written.reorder_point > 0
    # An upsert keyed on the natural key keeps the id it had, which is what lets
    # a rebuilt seed keep its own rows.
    refresh(tenant_a, sede_a)
    assert StockPolicy.objects.get(item=item, location=sede_a).id == written.id


def test_the_switch_stops_the_model_writing_thresholds(tenant_a, sede_a):
    """`write_model_stock_policies` is the safety valve, and turning it off
    stops the write rather than merely marking it."""
    item = make_item(tenant_a, "Omeprazol 20 mg × 30", tracks_lots=False)
    stock(tenant_a, sede_a, item, 300, days_back=210)
    for week in range(1, 21):
        sell(tenant_a, sede_a, item, weeks_back=week, units=25)

    tenant = Tenant.objects.get(id=tenant_a.id)
    purchasing_settings.write(tenant, {"write_model_stock_policies": False})
    refresh(tenant_a, sede_a)
    assert StockPolicy.objects.filter(source=PolicySource.MODEL).count() == 0


# ---------------------------------------------------------------------------
# The provenance line (acceptance 1, 31)
# ---------------------------------------------------------------------------


def test_the_training_window_is_computed_from_the_sales_that_exist(tenant_a, sede_a):
    """**Computed, never transcribed.** The drawn `18 meses` is what the
    handoff's own tenant would have produced; a tenant carrying six months must
    read `6 meses`, and where the two disagree the drawing yields."""
    item = make_item(tenant_a, "Salbutamol inhalador", tracks_lots=False)
    stock(tenant_a, sede_a, item, 100, days_back=210)
    sell(tenant_a, sede_a, item, weeks_back=25, units=4)
    sell(tenant_a, sede_a, item, weeks_back=1, units=4)

    with pin_tenant(tenant_a.id):
        window = forecast.training_window(tenant_a.id, sede_a.id)
    assert window["label"] == "6 meses"
    assert "18 meses" not in window["label"]


def test_a_three_week_window_reads_in_weeks(tenant_a, sede_a):
    """Under eight weeks the figure is whole weeks, which is what Usme's own
    three weeks produce on the seed."""
    item = make_item(tenant_a, "Suero oral", tracks_lots=False)
    stock(tenant_a, sede_a, item, 100, days_back=30)
    sell(tenant_a, sede_a, item, weeks_back=3, units=2)
    sell(tenant_a, sede_a, item, weeks_back=1, units=2)

    with pin_tenant(tenant_a.id):
        window = forecast.training_window(tenant_a.id, sede_a.id)
    assert window["label"].endswith("semanas")


def test_a_tenant_with_no_sales_has_no_window_at_all(tenant_a, sede_a):
    """The third form of the provenance line, and it names no figure: `Sin
    histórico cargado · sugerido por parámetros de la sede`."""
    with pin_tenant(tenant_a.id):
        assert forecast.training_window(tenant_a.id, sede_a.id)["label"] == ""


# ---------------------------------------------------------------------------
# Seasonality (acceptance 25's negative)
# ---------------------------------------------------------------------------


def test_a_tenant_under_a_year_old_has_no_seasonal_multiplier(tenant_a, sede_a):
    """**A model that invents a pollen season out of eleven weeks of data is
    worse than one that says `Rotación estable`.**"""
    item = make_item(tenant_a, "Loratadina 10 mg × 10", tracks_lots=False)
    stock(tenant_a, sede_a, item, 100, days_back=210)
    for week in range(1, 21):
        sell(tenant_a, sede_a, item, weeks_back=week, units=10)

    with pin_tenant(tenant_a.id):
        assert forecast.category_multipliers(tenant_a.id, timezone.localdate()) == {}


# ---------------------------------------------------------------------------
# Imported history (acceptance 15's positive half)
# ---------------------------------------------------------------------------


def test_imported_weeks_teach_the_forecast_and_drop_a_confidence_band(tenant_a, sede_a):
    """The two consumers that read `imported` name it explicitly, and this is
    one of them. An imported-majority window drops a band, because an imported
    week cannot be censored for stockouts."""
    item = make_item(tenant_a, "Atorvastatina 20 mg × 30", tracks_lots=False)
    stock(tenant_a, sede_a, item, 400, days_back=210)
    for week in range(1, 21):
        sell(tenant_a, sede_a, item, weeks_back=week, units=30, imported=True)

    refresh(tenant_a, sede_a)
    fresh = row(tenant_a, sede_a, item)
    assert fresh.imported_share == Decimal("1.000")
    assert forecast.band(fresh.confidence) != "alta"


def test_a_lot_tracked_item_is_forecast_from_its_whole_shelf(tenant_a, sede_a):
    """Coverage is a figure about a reference at a sede, not about one lot: the
    shelf is the sum across lots."""
    item = make_item(tenant_a, "Amoxicilina 500 mg × 20")
    first = make_lot(tenant_a, item, code="L-1")
    second = make_lot(tenant_a, item, code="L-2", days=600)
    stock(tenant_a, sede_a, item, 60, lot=first, days_back=210)
    stock(tenant_a, sede_a, item, 40, lot=second, days_back=209)
    for week in range(1, 21):
        sell(tenant_a, sede_a, item, weeks_back=week, units=10)

    refresh(tenant_a, sede_a)
    fresh = row(tenant_a, sede_a, item)
    assert fresh.coverage_days is not None
    # 100 units against ten a week is ten weeks of cover, to the day.
    assert Decimal("60") < fresh.coverage_days < Decimal("80")


def test_every_stocked_reference_gets_a_row_in_every_regime(tenant_a, sede_a):
    """*Jobs* · **there is no such thing as an item the forecast skips because
    it has no history.**"""
    for index in range(4):
        item = make_item(tenant_a, f"Referencia {index}", tracks_lots=False)
        stock(tenant_a, sede_a, item, 10 + index, days_back=210)
    refresh(tenant_a, sede_a)
    assert DemandForecast.objects.count() == Item.objects.count()
    assert DemandForecast.objects.filter(model_version="").count() == 0
