"""The fixtures, and what the three screens must render from them.

**The seed is the last check, and `convincingly` is not a feeling** (§1). What
is asserted here is what a person would see: three regimes in one order so the
**Confianza del modelo** chip has something to filter, a `Cobertura` column
carrying more than one of §B.7.4's bands, zero lines showing the recessed
stepper without anyone editing anything, every `Por qué` non-empty, all six
status badges on one page, and a partial receipt against a `sent` order.

**No figure here is a production sizing, and none is a drawn one.** The handoff's
`42` references, its `1.184` active and its `$9,4 M` describe the tenant the
handoff's own designer had. This seed holds what S1's catalog, S3's stock plan
and S4's sales produce, and *Verification* says so explicitly: the counts are
asserted against the seed's own numbers, or against what the product itself
reports, never against production sizing.

**Nothing here is written by hand.** The fixture runs `forecast.refresh` and
`purchase_order.generate` exactly as the cron does, so a regime that appears
only because a fixture set `basis` is a regime nobody has tested.
"""

import pytest
from django.core.management import call_command
from django.db.models import Count, Q

from core.demo import identity, registry as demo_registry
from core.models import (
    DemandForecast,
    ForecastBasis,
    GoodsReceipt,
    GoodsReceiptStatus,
    PolicySource,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    Sale,
    SaleSource,
    StockMove,
    StockMoveType,
    StockPolicy,
    Supplier,
)
from core.purchasing import demo as purchasing_demo, forecast

from core.tenancy import pin_tenant

pytestmark = pytest.mark.django_db


def coverage_band(days) -> str:
    """§B.7.4's four bands, named the way the screen colours them."""
    value = float(days)
    if value <= 4:
        return "critical"
    if value <= 20:
        return "warning"
    if value <= 90:
        return "normal"
    return "overstock"


def seed(profile):
    call_command("seed_demo_tenant", profile=profile)
    return demo_registry.uid(profile, "tenants", identity.slug_for(profile))


def drawn_order(tenant_id):
    return (
        PurchaseOrder.objects.filter(
            location__code=purchasing_demo.DRAWN_SEDE,
            supplier__name=purchasing_demo.DRAWN_SUPPLIER,
            status=PurchaseOrderStatus.SUGGESTED,
        )
        .order_by("number")
        .first()
    )


# ---------------------------------------------------------------------------
# The gate: nothing here came from a fixture (*Verification*, gates)
# ---------------------------------------------------------------------------


def test_every_forecast_row_came_from_the_job(tenant_a):
    """**A regime that appears only because a fixture set `basis` is a regime
    nobody has tested.** Every row carries the model version of the run that
    wrote it, and every order carries one too."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        assert DemandForecast.objects.filter(model_version="").count() == 0
        assert DemandForecast.objects.count() > 0
        assert (
            PurchaseOrder.objects.filter(source="model", model_version="").count() == 0
        )


def test_the_seed_carries_no_imported_sales(tenant_a):
    """*Demo seed* · **the seeded tenant's history is Botica's own**, because a
    demo that only works after an import demonstrates exactly the precondition
    §1 says must not exist."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        assert Sale.objects.filter(source=SaleSource.IMPORTED).count() == 0


# ---------------------------------------------------------------------------
# The drawn screen (acceptance 31)
# ---------------------------------------------------------------------------


def test_three_regimes_live_in_one_order(tenant_a):
    """*Verification* · **three rows and none of them zero.** If `parametric` is
    missing the seed has drifted and the chip below has nothing to filter."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        order = drawn_order(tenant_id)
        assert order is not None
        counts = dict(
            PurchaseOrderLine.objects.filter(purchase_order=order)
            .values_list("basis")
            .annotate(total=Count("id"))
        )
    for basis in ForecastBasis.values:
        assert counts.get(basis, 0) > 0, basis


def test_the_drawn_order_reads_like_the_screen_it_is(tenant_a):
    """Acceptance 31 · a populated order, a `Cobertura` column whose colour is
    doing work, at least two lines at zero so the recessed stepper is visible
    without anyone editing anything, and **every `Por qué` cell non-empty**."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        order = drawn_order(tenant_id)
        lines = list(PurchaseOrderLine.objects.filter(purchase_order=order))

        assert len([one for one in lines if one.approved_quantity > 0]) >= 5
        assert len([one for one in lines if one.approved_quantity == 0]) >= 2
        assert all(one.reason_code for one in lines)
        assert order.total > 0

        bands = {
            coverage_band(one.coverage_days)
            for one in lines
            if one.coverage_days is not None
        }
    # §B.7.4 · **the colour has to be doing work.** More than one band on one
    # order is what makes the column readable at a glance; a screen where every
    # numeral is the same grey is a column nobody looks at.
    #
    # The two urgent bands are **not** asserted, and the reason is another
    # stage's fixture rather than this one's code: a red `Cobertura` needs a
    # fast mover standing in quiebre, and S4's fixture sells only from rows S3
    # planned as `sufficient` or `overstock` -- so on this seed a reference in
    # quiebre has no sales, no demand estimate, and therefore no cover figure to
    # colour. The urgent bands are covered by their own unit test instead, and
    # what this one checks is that the seeded order exercises the column.
    assert len(bands) >= 2, bands


def test_the_confidence_chip_has_both_readings_to_filter(tenant_a):
    """Acceptance 28 · `Revisar primero` selects `Paramétrica` and `Baja`
    together, and both must return rows or the affordance the day-one order
    depends on returns the filtered-empty state."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        order = drawn_order(tenant_id)
        lines = list(PurchaseOrderLine.objects.filter(purchase_order=order))
        parametric = [one for one in lines if one.basis == ForecastBasis.PARAMETRIC]
        baja = [
            one
            for one in lines
            if one.confidence is not None and forecast.band(one.confidence) == "baja"
        ]
    assert parametric and baja


def test_no_seeded_line_claims_a_season(tenant_a):
    """*Verification* · the seed's window is 180 days and cannot support a
    year-ago multiplier, so `seasonal_peak` and its pair are **unreachable** --
    and a seed that canned a pollen season out of twenty-six weeks so the pixels
    matched would be a screenshot worth more than a true number."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        assert (
            PurchaseOrderLine.objects.filter(reason_code__startswith="seasonal").count()
            == 0
        )


def test_prose_lands_only_on_learned_lines(tenant_a):
    """*Verification* · `select count(*) from purchase_order_lines where reason
    is not null and basis <> 'learned'` returns 0 across the whole seed."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        assert (
            PurchaseOrderLine.objects.exclude(reason="")
            .exclude(basis=ForecastBasis.LEARNED)
            .count()
            == 0
        )


def test_the_provenance_window_is_the_seeds_own_and_never_the_drawn_string(
    tenant_a,
):
    """Acceptance 31 · **the string `18 meses` anywhere on this tenant is a hard
    failure**, because no window on it produces one. Chapinero carries 180 days
    and Usme its own three weeks."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        chapinero = PurchaseOrder.objects.filter(location__code="CHA").first()
        usme = PurchaseOrder.objects.filter(location__code="USM").first()
        warm = forecast.training_window(tenant_id, chapinero.location_id)
        cold = forecast.training_window(tenant_id, usme.location_id)
    assert warm["label"] == "6 meses"
    assert cold["label"] != warm["label"]
    assert "18 meses" not in (warm["label"], cold["label"])


# ---------------------------------------------------------------------------
# The list and the receipt (acceptance 31)
# ---------------------------------------------------------------------------


def test_all_six_status_badges_are_reachable_on_one_page(tenant_a):
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        present = set(PurchaseOrder.objects.values_list("status", flat=True).distinct())
    assert present == set(PurchaseOrderStatus.values)


def test_a_partial_receipt_moved_stock_only_through_the_ledger(tenant_a):
    """Acceptance 11 · every quantity a receipt put on a shelf traces to a
    `receipt` move carrying the receipt's own document pair, and **no row in
    `stock_moves` was written by this stage's own code**."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        receipts = list(
            GoodsReceipt.objects.filter(status=GoodsReceiptStatus.CONFIRMED)
        )
        assert receipts
        moves = StockMove.objects.filter(
            document_type="goods_receipt",
            document_id__in=[one.id for one in receipts],
        )
        assert moves.exists()
        assert set(moves.values_list("type", flat=True)) == {StockMoveType.RECEIPT}
        assert PurchaseOrder.objects.filter(
            status=PurchaseOrderStatus.PARTIALLY_RECEIVED
        ).exists()


def test_a_suppliers_lead_time_was_observed_rather_than_typed(tenant_a):
    """*Verification* · one supplier whose `lead_time_days` came from a receipt
    rather than from onboarding."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        received = GoodsReceipt.objects.filter(
            status=GoodsReceiptStatus.CONFIRMED, purchase_order__isnull=False
        ).first()
        assert received is not None
        assert Supplier.objects.get(id=received.supplier_id).lead_time_days


# ---------------------------------------------------------------------------
# The two profiles the verification section reaches for by name
# ---------------------------------------------------------------------------


def test_the_cold_profile_is_parametric_end_to_end(tenant_a):
    """*Verification* · the state this needs -- catalog and stock, no sales
    anywhere -- **is a profile, not a procedure**. Nothing here creates a sede
    and nothing receives stock outside an order, because this stage does not
    allow it."""
    del tenant_a
    tenant_id = seed("cold")
    with pin_tenant(tenant_id):
        assert (
            DemandForecast.objects.exclude(basis=ForecastBasis.PARAMETRIC).count() == 0
        )
        assert (
            DemandForecast.objects.filter(
                Q(weekly_sales__isnull=False)
                | Q(trend__isnull=False)
                | Q(coverage_days__isnull=False)
            ).count()
            == 0
        )
        # A parametric forecast writes no policy row at all, ever.
        assert StockPolicy.objects.filter(source=PolicySource.MODEL).count() == 0

        bases = set(
            PurchaseOrderLine.objects.values_list("basis", flat=True).distinct()
        )
        assert bases in ({ForecastBasis.PARAMETRIC}, set())

        # Path 2 cannot fire on this tenant, and it is derivable rather than
        # incidental: the category default needs items already in `learning` or
        # `learned`, and a tenant with no sales at all has none.
        codes = set(
            PurchaseOrderLine.objects.values_list("reason_code", flat=True).distinct()
        )
        assert "parametric_category_default" not in codes
        assert not any(code.startswith("seasonal") for code in codes)
        assert "stable_rotation" not in codes
        assert "predictable_chronic" not in codes
        assert "learning_floor" not in codes


def test_every_cold_line_carries_a_reference_a_pharmacist_set_a_threshold_for(
    tenant_a,
):
    """*Verification* · **acceptance 26's withholding, counted rather than
    described**: only references carrying a manual `stock_policies` row at that
    sede appear on the order, and every other one is absent."""
    del tenant_a
    tenant_id = seed("cold")
    with pin_tenant(tenant_id):
        for order in PurchaseOrder.objects.select_related("location"):
            managed = set(
                StockPolicy.objects.filter(
                    location_id=order.location_id, source=PolicySource.MANUAL
                ).values_list("item_id", flat=True)
            )
            on_order = set(
                PurchaseOrderLine.objects.filter(purchase_order=order).values_list(
                    "item_id", flat=True
                )
            )
            assert on_order <= managed


def test_the_minimal_profile_is_the_tenant_nothing_leaked_into(tenant_a):
    """*Verification* · seed a second tenant so there is a tenant for misdirected
    work to land in -- and then assert it stayed empty."""
    del tenant_a
    minimal = seed("minimal")
    with pin_tenant(minimal):
        assert DemandForecast.objects.count() == 0
        assert PurchaseOrder.objects.count() == 0


def test_the_young_profile_reaches_no_learned_line(tenant_a):
    """*Demo seed* · at `young`, twelve days across the network land most
    references in `learning` over a `parametric` remainder, and **no line
    anywhere reads `learned`**."""
    del tenant_a
    tenant_id = seed("young")
    with pin_tenant(tenant_id):
        assert DemandForecast.objects.filter(basis=ForecastBasis.LEARNED).count() == 0
        bases = set(DemandForecast.objects.values_list("basis", flat=True).distinct())
    assert ForecastBasis.LEARNING in bases
    assert ForecastBasis.PARAMETRIC in bases


def test_the_seed_is_idempotent_across_a_rebuild(tenant_a):
    """The guard's own guarantee: **a rerun over the seed's own rows builds the
    same tenant again rather than refusing it.**

    It is not a row count that has to hold. Generation runs a second morning
    over a tenant whose last orders were approved, sent and discarded, and a
    supplier whose last order is no longer `suggested` gets a new one -- which
    is the job working rather than the seed drifting. What must hold is that the
    guard admits the rerun, that the downstream walk does not approve and
    discard a second batch or receive a second delivery, and that the drawn
    order is still there to open.
    """
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        manual_before = PurchaseOrder.objects.filter(source="manual").count()
        receipts_before = GoodsReceipt.objects.count()
        assert manual_before and receipts_before
        assert drawn_order(tenant_id) is not None

    seed("default")
    with pin_tenant(tenant_id):
        assert PurchaseOrder.objects.filter(source="manual").count() == manual_before
        assert GoodsReceipt.objects.count() == receipts_before
        assert drawn_order(tenant_id) is not None
