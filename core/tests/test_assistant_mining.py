"""What the miner counts, stated so a rule can be argued with.

A **ticket** is one closed sale with at least two `sale_lines` over distinct
items, at `source IN ('counter','imported')`, inside the window measured on
`recorded_at`. `support` is the number of tickets containing both items;
`confidence` is P(B present | A present); `lift` is `confidence` over the share
of all tickets containing B.
"""

from decimal import Decimal

import pytest

from core.assistant import mining
from core.assistant import settings as assistant_settings
from core.models import (
    CrossSellBasis,
    CrossSellConfidence,
    CrossSellRule,
    SaleSource,
    Tenant,
)
from core.tenancy import pin_tenant
from core.tests.conftest import make_location
from core.tests.test_counter_push import Till, apply, price, stock
from core.tests.test_inventory_ledger import make_lot
from core.tests.test_sync_pull import make_device, make_item

pytestmark = pytest.mark.django_db


def shelf(tenant, location, names, device=None):
    held = {}
    for name in names:
        item = make_item(tenant, name)
        lot = make_lot(tenant, item, code=f"L-{name[:6]}")
        price(tenant, item, "3900")
        stock(tenant, location, item, lot, 5000, device=device)
        held[name] = (item, lot)
    return held


def ring(device, user, held, names, *, number):
    """One closed ticket carrying the named references."""
    till = Till(device, user)
    rows = [till.open_shift(), till.open_sale(number=number)]
    for position, name in enumerate(names):
        item, lot = held[name]
        rows.append(till.line(item, 1, "3900", lot=lot, position=position))
    rows.append(till.payment("cash", str(3900 * len(names))))
    rows.append(till.close_sale())
    apply(device, rows, batch_id=f"batch-{number}")


def configure(tenant, **values):
    with pin_tenant(tenant.id):
        return assistant_settings.write(Tenant.objects.get(id=tenant.id), values)


def test_the_miner_counts_directed_pairs_and_reports_its_own_denominator(
    tenant_a, sede_a, cashier_a
):
    """A→B and B→A are two rows with different confidences, because the anchor
    is what the customer is being sold and the suggestion is what goes with
    it — averaging the two directions loses exactly the asymmetry the assistant
    uses."""
    device, _key = make_device(tenant_a, sede_a)
    held = shelf(tenant_a, sede_a, ["Suero", "Antidiarreico", "Analgésico"], device)
    for index in range(6):
        ring(device, cashier_a, held, ["Suero", "Antidiarreico"], number=f"C1-{index}")
    for index in range(4):
        ring(device, cashier_a, held, ["Suero", "Analgésico"], number=f"C1-9{index}")

    configure(tenant_a, cross_sell_min_support=3, cross_sell_min_confidence=0.1)
    with pin_tenant(tenant_a.id):
        report = mining.refresh(Tenant.objects.get(id=tenant_a.id))
        rows = {
            (row.item_a.name, row.item_b.name): row
            for row in CrossSellRule.objects.filter(
                location_id=sede_a.id
            ).select_related("item_a", "item_b")
        }
    assert report.written > 0
    suero_to_anti = rows[("Suero", "Antidiarreico")]
    anti_to_suero = rows[("Antidiarreico", "Suero")]
    # Ten tickets carry suero, six of them carry the antidiarrheal; all six
    # antidiarrheal tickets carry suero.
    assert suero_to_anti.support == 6
    assert suero_to_anti.confidence == Decimal("0.6000")
    assert anti_to_suero.confidence == Decimal("1.0000")
    assert suero_to_anti.ticket_count == 10
    assert suero_to_anti.basis == CrossSellBasis.COUNTER
    assert suero_to_anti.window == "90d"


def test_lift_is_confidence_over_the_share_of_tickets_carrying_b(
    tenant_a, sede_a, cashier_a
):
    """Confidence is P(B|A), and a product almost every ticket carries scores
    high against everything. Ranking on lift is what stops every suggestion card
    in the product being the same three fast movers."""
    device, _key = make_device(tenant_a, sede_a)
    held = shelf(tenant_a, sede_a, ["Bolsa", "Suero", "Antidiarreico"], device)
    # The bag is on every ticket; the pair that means something is on four.
    for index in range(4):
        ring(
            device,
            cashier_a,
            held,
            ["Suero", "Antidiarreico", "Bolsa"],
            number=f"C1-{index}",
        )
    for index in range(6):
        ring(device, cashier_a, held, ["Suero", "Bolsa"], number=f"C1-9{index}")

    configure(tenant_a, cross_sell_min_support=3, cross_sell_min_confidence=0.1)
    with pin_tenant(tenant_a.id):
        mining.refresh(Tenant.objects.get(id=tenant_a.id))
        rows = {
            (row.item_a.name, row.item_b.name): row
            for row in CrossSellRule.objects.filter(
                location_id=sede_a.id
            ).select_related("item_a", "item_b")
        }
    # P(Bolsa) is 1, so no anchor gets any lift from carrying it.
    assert rows[("Suero", "Bolsa")].lift == Decimal("1.0000")
    # P(Antidiarreico) is 0,4, and every antidiarrheal ticket carries suero.
    assert rows[("Antidiarreico", "Suero")].lift == Decimal("1.0000")
    assert rows[("Suero", "Antidiarreico")].lift == Decimal("1.0000")
    assert rows[("Bolsa", "Antidiarreico")].lift == Decimal("1.0000")


def test_a_single_line_ticket_carries_no_pair_and_a_returned_line_is_excluded(
    tenant_a, sede_a, cashier_a
):
    """*What breaks if this is ignored:* a legacy export that recorded one line
    per ticket produces no pairs at all, and the miner reports a support floor
    nothing clears rather than looking like a broken job."""
    device, _key = make_device(tenant_a, sede_a)
    held = shelf(tenant_a, sede_a, ["Suero", "Antidiarreico"], device)
    for index in range(5):
        ring(device, cashier_a, held, ["Suero"], number=f"C1-{index}")
    configure(tenant_a, cross_sell_min_support=1, cross_sell_min_confidence=0.0)
    with pin_tenant(tenant_a.id):
        report = mining.refresh(Tenant.objects.get(id=tenant_a.id))
        assert CrossSellRule.objects.count() == 0
    assert report.written == 0


def test_a_run_that_finds_nothing_writes_nothing_and_moves_no_timestamp(
    tenant_a, sede_a, cashier_a
):
    """**A run that finds nothing above the floor is a pass, not a failure.** A
    job that only starts once there is history is a job nobody remembers to
    start (§1, *Cold start*)."""
    device, _key = make_device(tenant_a, sede_a)
    held = shelf(tenant_a, sede_a, ["Suero", "Antidiarreico"], device)
    for index in range(3):
        ring(device, cashier_a, held, ["Suero", "Antidiarreico"], number=f"C1-{index}")

    configure(tenant_a, cross_sell_min_support=2, cross_sell_min_confidence=0.1)
    with pin_tenant(tenant_a.id):
        mining.refresh(Tenant.objects.get(id=tenant_a.id))
        first = list(
            CrossSellRule.objects.order_by("id").values_list("computed_at", flat=True)
        )
    assert first

    configure(tenant_a, cross_sell_min_support=99)
    with pin_tenant(tenant_a.id):
        report = mining.refresh(Tenant.objects.get(id=tenant_a.id))
        after = list(
            CrossSellRule.objects.order_by("id").values_list("computed_at", flat=True)
        )
    assert report.written == 0
    assert after == first


def test_two_runs_over_one_window_produce_the_same_rows_and_respect_the_cap(
    tenant_a, sede_a, cashier_a
):
    """Acceptance 20 and check 13 · **the cap is enforced by the job that writes
    the rows, not by the predicate that reads them** — a cap enforced by the
    reader is a hope, because the table it is reading has already been
    written."""
    device, _key = make_device(tenant_a, sede_a)
    names = ["Suero", "A", "B", "C", "D", "E"]
    held = shelf(tenant_a, sede_a, names, device)
    for index, partner in enumerate(names[1:]):
        for repeat in range(4):
            ring(
                device,
                cashier_a,
                held,
                ["Suero", partner],
                number=f"C1-{index}{repeat}",
            )
    configure(
        tenant_a,
        cross_sell_min_support=2,
        cross_sell_min_confidence=0.0,
        cross_sell_rules_per_item=2,
    )
    with pin_tenant(tenant_a.id):
        tenant = Tenant.objects.get(id=tenant_a.id)
        mining.refresh(tenant)
        first = _fingerprint()
        mining.refresh(tenant)
        again = _fingerprint()
        counts: dict = {}
        for location_id, anchor in CrossSellRule.objects.values_list(
            "location_id", "item_a_id"
        ):
            counts[(location_id, anchor)] = counts.get((location_id, anchor), 0) + 1
    assert first == again
    assert max(counts.values()) <= 2


def _fingerprint():
    """The rows as strings, so the network scope's null sorts beside the sedes'
    ids rather than refusing to compare with them."""
    return sorted(
        f"{location_id}|{anchor}|{partner}|{support}|{rank}"
        for location_id, anchor, partner, support, rank in CrossSellRule.objects.values_list(
            "location_id", "item_a_id", "item_b_id", "support", "rank"
        )
    )


def test_a_rule_that_stops_clearing_the_floor_departs_rather_than_disappearing(
    tenant_a, sede_a, cashier_a
):
    """S2 criterion 14 · **no row of a registry collection is ever hard-deleted.**

    A deleted row leaves nothing to serve a departure marker from and no
    `updated_at` to serve it at, so every till that already holds the rule keeps
    it forever. Its `support` is zeroed in place instead, which is below any
    floor, which is what the registry's own membership predicate reads.
    """
    device, _key = make_device(tenant_a, sede_a)
    held = shelf(tenant_a, sede_a, ["Suero", "Antidiarreico", "Analgésico"], device)
    for index in range(5):
        ring(device, cashier_a, held, ["Suero", "Antidiarreico"], number=f"C1-{index}")
    for index in range(2):
        ring(device, cashier_a, held, ["Suero", "Analgésico"], number=f"C1-9{index}")

    configure(tenant_a, cross_sell_min_support=2, cross_sell_min_confidence=0.1)
    with pin_tenant(tenant_a.id):
        mining.refresh(Tenant.objects.get(id=tenant_a.id))
        before = {
            (row.item_a.name, row.item_b.name): (row.id, row.updated_at)
            for row in CrossSellRule.objects.filter(
                location_id=sede_a.id
            ).select_related("item_a", "item_b")
        }
    assert ("Suero", "Analgésico") in before
    assert ("Suero", "Antidiarreico") in before

    # The floor rises past the thinner pair and the stronger one still clears,
    # so this is a refresh that **publishes** rather than a run that finds
    # nothing -- which is the case a departure has to survive.
    configure(tenant_a, cross_sell_min_support=4)
    with pin_tenant(tenant_a.id):
        mining.refresh(Tenant.objects.get(id=tenant_a.id))
        rows = {
            (row.item_a.name, row.item_b.name): row
            for row in CrossSellRule.objects.filter(
                location_id=sede_a.id
            ).select_related("item_a", "item_b")
        }
    # **The row is still there**, at zero support, with its cursor moved so the
    # till hears about it. Nothing was deleted.
    gone = rows[("Suero", "Analgésico")]
    assert gone.id == before[("Suero", "Analgésico")][0]
    assert gone.support == 0
    assert gone.updated_at > before[("Suero", "Analgésico")][1]
    assert rows[("Suero", "Antidiarreico")].support == 5


def test_the_band_is_derived_from_two_numbers_and_never_entered():
    """`confidence_band` is **not** `confidence`. §1 asks every model surface to
    show a confidence, and a screen that rendered P(B|A) under a `Confianza del
    modelo` label has rendered the wrong number."""
    assert mining.band(support=40, ticket_count=220) == CrossSellConfidence.LOW
    # A solid-looking ratio out of a window that barely exists is still `low`.
    assert mining.band(support=150, ticket_count=900) == CrossSellConfidence.LOW
    assert mining.band(support=150, ticket_count=5_000) == CrossSellConfidence.MEDIUM
    assert mining.band(support=800, ticket_count=5_000) == CrossSellConfidence.MEDIUM
    assert mining.band(support=800, ticket_count=20_000) == CrossSellConfidence.HIGH


def test_each_sede_and_the_network_are_mined_as_their_own_scopes(
    tenant_a, sede_a, cashier_a
):
    """The till holds both and the ranker prefers the sede row where one exists,
    which is what makes *"en este punto el 64%"* a true sentence."""
    other = make_location(tenant_a, "SUB", "Suba")
    device_a, _one = make_device(tenant_a, sede_a)
    device_b, _two = make_device(tenant_a, other, label="Caja 2")
    held = shelf(tenant_a, sede_a, ["Suero", "Antidiarreico"], device_a)
    for name, (item, lot) in held.items():
        del name
        stock(tenant_a, other, item, lot, 500, device=device_b)
    for index in range(4):
        ring(
            device_a, cashier_a, held, ["Suero", "Antidiarreico"], number=f"C1-{index}"
        )
    for index in range(3):
        ring(
            device_b, cashier_a, held, ["Suero", "Antidiarreico"], number=f"C2-{index}"
        )

    configure(tenant_a, cross_sell_min_support=3, cross_sell_min_confidence=0.1)
    with pin_tenant(tenant_a.id):
        mining.refresh(Tenant.objects.get(id=tenant_a.id))
        network = CrossSellRule.objects.filter(location__isnull=True)
        assert network.exists()
        assert network.first().ticket_count == 7
        assert CrossSellRule.objects.filter(location_id=sede_a.id).exists()
        assert CrossSellRule.objects.filter(location_id=other.id).exists()


def test_an_imported_population_is_labelled_rather_than_hidden(
    tenant_a, sede_a, cashier_a
):
    """**Imported history is used and is labelled.** Where S6's loader has run
    the window spans two systems with two definitions of a ticket, so `basis`
    records which sources the run consumed."""
    device, _key = make_device(tenant_a, sede_a)
    held = shelf(tenant_a, sede_a, ["Suero", "Antidiarreico"], device)
    for index in range(4):
        ring(device, cashier_a, held, ["Suero", "Antidiarreico"], number=f"C1-{index}")
    with pin_tenant(tenant_a.id):
        from core.models import Sale

        # S4's own constraint: an imported sale names no shift and no device,
        # because it happened on a system this one did not run.
        Sale.objects.filter(number="C1-0").update(
            source=SaleSource.IMPORTED, shift=None, device=None
        )
    configure(tenant_a, cross_sell_min_support=2, cross_sell_min_confidence=0.1)
    with pin_tenant(tenant_a.id):
        mining.refresh(Tenant.objects.get(id=tenant_a.id))
        assert set(CrossSellRule.objects.values_list("basis", flat=True)) == {
            CrossSellBasis.MIXED
        }
