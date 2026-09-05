"""S7's engines, its gates and the negative the whole stage rests on (A11).

**That it cannot write a price is what this stage is graded on.** The first
block below is that check in three forms -- the run leaves `item_prices`
untouched, the routes that used to exist return 404 rather than 403, and no
module under `core/pricing/` contains a write against that table at all. A 403
is a route standing behind a policy somebody can change, which is exactly what
the amendment replaced with a property of the schema.
"""

import json
import pathlib
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from core.catalog import prices as price_service
from core.models import (
    AuditLog,
    CapStatus,
    ElasticityEstimate,
    ElasticityStatus,
    ItemPrice,
    NoProposalReason,
    PriceProposal,
    PriceProposalStatus,
    PriceSource,
    ProposalBasis,
    Supplier,
    SupplierItem,
)
from core.pricing import caps, engine, estimator, reasons
from core.pricing import settings as pricing_settings
from core.tests.test_catalog import make_item

pytestmark = pytest.mark.django_db


def _post(client, path, payload=None):
    return client.post(
        path, data=json.dumps(payload or {}), content_type="application/json"
    )


def _put(client, path, payload):
    return client.put(path, data=json.dumps(payload), content_type="application/json")


def priced(tenant, *, price, cost, name="Acetaminofén 500 mg × 100", **overrides):
    """One reference with a cost basis and an in-force network price.

    The cost comes from a supplier link rather than a lot, because a lot needs a
    location and a projection row and this stage reads neither -- it reads a
    cost, and `cost_bases` falls back to the supplier's list where there is no
    stock.
    """
    item = make_item(tenant, name=name, presentation=f"caja {name[-3:]}", **overrides)
    ItemPrice.objects.create(
        tenant=tenant,
        item=item,
        price=Decimal(price),
        effective_from=timezone.localdate() - timedelta(days=200),
        source=PriceSource.IMPORTED,
    )
    supplier, _ = Supplier.objects.get_or_create(tenant=tenant, name="Coopidrogas")
    SupplierItem.objects.create(
        tenant=tenant, supplier=supplier, item=item, cost=Decimal(cost)
    )
    return item


def settings_for(tenant, **overrides):
    return pricing_settings.write(
        tenant,
        {
            "margin_goal_pct": 22.0,
            "max_single_step_pct": 3.0,
            "rounding_unit": 50,
            "min_days_between_changes": 30,
            "allow_raise_without_cap": False,
            **overrides,
        },
    )


def cap(item, price, status=CapStatus.CAPPED):
    item.regulated_max_price = None if price is None else Decimal(price)
    item.cap_status = status
    item.save(update_fields=["regulated_max_price", "cap_status"])
    return item


def live(item):
    return PriceProposal.objects.filter(
        item=item, status__in=("proposed", "above_cap")
    ).first()


def estimate_for(item):
    return ElasticityEstimate.objects.filter(item=item).order_by("-computed_at").first()


# ---------------------------------------------------------------------------
# A11 · the negative
# ---------------------------------------------------------------------------


def test_a_run_writes_no_price_row_at_all(tenant_a):
    """*Verification* · record `count(*)`, `max(updated_at)` and
    `max(effective_from)` over `item_prices`; run; read the three again --
    **identical**. Then the audit trail holds the run and no `item_prices` row.
    """
    settings_for(tenant_a)
    item = priced(tenant_a, price="12500", cost="8400")
    cap(item, "20000")

    def snapshot():
        rows = ItemPrice.objects.filter(tenant=tenant_a)
        return (
            rows.count(),
            rows.order_by("-updated_at").values_list("updated_at", flat=True).first(),
            rows.order_by("-effective_from")
            .values_list("effective_from", flat=True)
            .first(),
        )

    before = snapshot()
    engine.run(tenant_a.id)
    caps.check(tenant_a.id)
    assert snapshot() == before
    assert (
        AuditLog.objects.filter(tenant=tenant_a, entity_type="item_prices").count() == 0
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/pricing/proposals/approve",
        "/api/pricing/proposals/apply",
        "/api/pricing/proposals/revert",
        "/api/pricing/proposals/dismiss",
        "/api/pricing/apply",
        "/api/pricing/runs/apply",
    ],
)
def test_the_routes_that_used_to_exist_return_404_and_not_403(client_as, owner_a, path):
    """**404, not 403.** A 403 is a route standing behind a policy somebody can
    change, which is what the amendment replaced with a schema property."""
    assert _post(client_as(owner_a), path).status_code == 404


def test_no_module_in_this_stage_writes_item_prices():
    """The structural half of acceptance 11, read off the source.

    Revoking every privilege on `item_prices` is not expressible in a suite that
    shares one runtime role with S1's own editor -- so the property is asserted
    where it actually lives: no module under `core/pricing/` contains a write
    against that table, in the ORM or in SQL.
    """
    writes = re.compile(
        r"ItemPrice\.objects\.(create|update|bulk_create|delete)|"
        r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+item_prices",
        re.IGNORECASE,
    )
    for module in sorted(pathlib.Path("core/pricing").glob("*.py")):
        assert not writes.search(module.read_text()), module


def test_price_source_has_exactly_two_values():
    """A third is a rebuilt write path whatever it has been named (A11)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'price_source' ORDER BY e.enumsortorder"
        )
        assert [row[0] for row in cursor.fetchall()] == ["manual", "imported"]


# ---------------------------------------------------------------------------
# The estimator withholds rather than guessing
# ---------------------------------------------------------------------------


def test_an_unmoved_reference_has_a_null_elasticity_and_not_a_zero(tenant_a):
    """Acceptance 1 · **not `0`.** A zero is a claim of perfect inelasticity,
    and on a catalog where most references have never moved it turns every one
    of them into a maximum-step rise."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="12500", cost="11000")
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)

    row = estimate_for(item)
    assert row.elasticity is None
    assert row.status == ElasticityStatus.NO_SALES
    assert ElasticityEstimate.objects.filter(elasticity=0).count() == 0

    proposal = live(item)
    assert proposal.basis == ProposalBasis.MARGIN_RULE
    assert proposal.elasticity_estimate_id is None
    assert "Sin ventas" in reasons.elasticity_sentence(row)


def test_the_database_refuses_an_elasticity_of_exactly_zero(tenant_a):
    """The one number this table must never hold, refused by a CHECK rather than
    by a code review."""
    from django.db import IntegrityError, transaction

    item = priced(tenant_a, price="1000", cost="500")
    with pytest.raises(IntegrityError), transaction.atomic():
        ElasticityEstimate.objects.create(
            tenant=tenant_a,
            item=item,
            elasticity=Decimal("0"),
            window="26w",
            status=ElasticityStatus.ESTIMATED,
            model_version="x",
        )


def test_a_fit_that_is_not_a_demand_response_is_withheld():
    """A positive β is the trend wearing an elasticity's clothes, and a β past
    the plausibility ceiling is not a droguería reference. Both are withheld,
    never clamped: a clamped coefficient is a fabricated one."""
    assert estimator._is_a_demand_response(-0.34)
    assert not estimator._is_a_demand_response(0.0)
    assert not estimator._is_a_demand_response(283.29)
    assert not estimator._is_a_demand_response(-8.05)


def test_every_evaluated_item_gets_a_row_even_when_nothing_can_be_said(tenant_a):
    """An item absent from this table is an item that vanished from the screen
    without saying so."""
    priced(tenant_a, price="9000", cost="6000", name="Con costo")
    bare = make_item(tenant_a, name="Sin costo", presentation="caja")
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=bare,
        price=Decimal("5000"),
        effective_from=timezone.localdate() - timedelta(days=10),
        source=PriceSource.IMPORTED,
    )
    settings_for(tenant_a)
    engine.run(tenant_a.id)

    assert ElasticityEstimate.objects.filter(tenant=tenant_a).count() == 2
    assert estimate_for(bare).status == ElasticityStatus.NO_COST


# ---------------------------------------------------------------------------
# The margin rule, worked once
# ---------------------------------------------------------------------------


def test_the_margin_rule_reaches_the_goal_in_steps(tenant_a):
    """Acceptance 31 · Loratadina, from the stage document's own worked example.

    Cost `$3.900`, price `$4.600`, goal 22,0%, step cap 3,0%, rounding 50. The
    goal price is `$5.000`, past the cap, so the candidate is `$4.738` rounded
    **toward the current price** to `$4.700` -- never `$4.750`.
    """
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900", name="Loratadina 10 mg × 10")
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)

    proposal = live(item)
    assert proposal.suggested_price == Decimal("4700.00")
    assert proposal.basis == ProposalBasis.MARGIN_RULE
    assert proposal.confidence == "low"
    assert proposal.reason_code == "margin_below_goal"
    assert proposal.current_margin == Decimal("15.22")
    assert proposal.projected_margin == Decimal("17.02")
    assert proposal.margin_gap_pp == Decimal("4.98")
    assert proposal.estimated_monthly_impact is None  # no trailing volume
    assert proposal.trailing_monthly_units is None


def test_rounding_never_carries_a_step_past_the_policy_limit(tenant_a):
    """Acceptance 22 · `$12.500` is suggested at `$12.850` and never `$12.900`.

    A rounding rule that can exceed a stated policy limit is a rounding rule
    that quietly rewrites the policy -- and the limit is the owner's.
    """
    assert engine.round_toward(
        Decimal("12875"), Decimal("12500"), Decimal("50"), Decimal("3.0")
    ) == Decimal("12850")
    assert engine.round_toward(
        Decimal("4738"), Decimal("4600"), Decimal("50"), Decimal("3.0")
    ) == Decimal("4700")


def test_the_rounding_unit_is_the_price_a_customer_pays(tenant_a):
    """A `$50` unit on a tablet at `$445` is a step no fraccionable reference
    could ever clear, because the whole 3% budget is smaller than one unit."""
    box = make_item(tenant_a, name="Caja", presentation="caja", splittable=False)
    tablet = make_item(
        tenant_a,
        name="Tableta",
        presentation="blíster",
        splittable=True,
        units_per_pack=30,
    )
    assert engine.rounding_step(box, 50) == Decimal("50.00")
    assert engine.rounding_step(tablet, 50) == Decimal("1.67")


def test_a_reference_at_or_above_the_goal_is_left_alone(tenant_a):
    """Acceptance 21 · `at_margin_goal`, and it is **not** the same badge as a
    reference nothing could evaluate."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="10000", cost="7590")  # 24,1%
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)

    assert live(item) is None
    row = estimate_for(item)
    assert row.no_proposal_reason == NoProposalReason.AT_MARGIN_GOAL
    sentence = reasons.no_proposal_sentence(
        row.no_proposal_reason,
        proposal_figures={"margin": Decimal("24.10"), "goal": Decimal("22.0")},
    )
    assert sentence.startswith("Margen 24,1%, por encima de la meta de 22,0%")


def test_with_no_margin_goal_the_margin_rule_proposes_nothing(tenant_a):
    """Acceptance 33 · **the day-one state, not an edge case.** The grid still
    renders and the empty state points at the settings field."""
    pricing_settings.write(tenant_a, {"max_single_step_pct": 3.0})
    item = priced(tenant_a, price="4600", cost="3900")
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)

    assert (
        PriceProposal.objects.filter(
            tenant=tenant_a, basis=ProposalBasis.MARGIN_RULE
        ).count()
        == 0
    )
    assert pricing_settings.read(tenant_a)["margin_goal_pct"] is None


# ---------------------------------------------------------------------------
# The cap refuses
# ---------------------------------------------------------------------------


def test_an_unknown_cap_blocks_a_raise_from_either_engine(tenant_a):
    """Acceptance 5 · the default, and the day-one behaviour §11.4 governs.

    A null cap means *unknown*, never *uncapped*, so the margin rule proposes
    nothing upward either.
    """
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    engine.run(tenant_a.id)

    assert live(item) is None
    assert estimate_for(item).no_proposal_reason == NoProposalReason.CAP_BLOCKS_RAISE

    settings_for(tenant_a, allow_raise_without_cap=True)
    engine.run(tenant_a.id)
    proposal = live(item)
    assert proposal is not None
    assert proposal.suggested_price > proposal.current_price
    # `respects_regulated_cap` stays false: a boolean named *respects the
    # regulated cap* cannot be true against a cap nobody holds.
    assert proposal.respects_regulated_cap is False


def test_a_capped_reference_is_proposed_at_its_cap_and_says_so(tenant_a):
    """*Verification* · `suggested_price = regulated_max_price_at_proposal` with
    `reason_code = cap_bound_raise`, and **never above it, ever**."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    cap(item, "4680")
    engine.run(tenant_a.id)

    proposal = live(item)
    assert proposal.suggested_price == Decimal("4680.00")
    assert proposal.regulated_max_price_at_proposal == Decimal("4680.00")
    assert proposal.reason_code == "cap_bound_raise"
    assert proposal.respects_regulated_cap is True
    assert (
        PriceProposal.objects.filter(
            tenant=tenant_a,
            suggested_price__gt=Decimal("4680"),
            regulated_max_price_at_proposal=Decimal("4680"),
        ).count()
        == 0
    )


def test_a_price_already_above_a_loaded_cap_is_a_compliance_finding(tenant_a):
    """Acceptance 4 · `above_cap` on the critical tint, and **no action at all**.

    A price above a legal ceiling is not a pricing opportunity, and the row
    carries no way into the price editor at any role.
    """
    settings_for(tenant_a)
    item = priced(tenant_a, price="19400", cost="9000")
    cap(item, "18900")
    engine.run(tenant_a.id)

    proposal = live(item)
    assert proposal.status == PriceProposalStatus.ABOVE_CAP
    assert proposal.respects_regulated_cap is False
    assert proposal.reason_code == "above_regulated_cap"
    assert "supera el tope regulado de $18.900" in reasons.proposal_sentence(proposal)


def test_the_daily_check_finds_a_cap_that_moved_without_a_model_run(tenant_a):
    """Acceptance 20 · a CNPMDM circular lowers a ceiling and a price that was
    legal on Tuesday is not on Wednesday. Tying that to a weekly run would leave
    a pharmacy up to six days over the line."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="19400", cost="16000")
    cap(item, "25000")
    engine.run(tenant_a.id)
    assert live(item).status == PriceProposalStatus.PROPOSED

    cap(item, "18900")
    report = caps.check(tenant_a.id)
    assert report["raised"] == 1
    assert live(item).status == PriceProposalStatus.ABOVE_CAP


def test_a_price_editor_refuses_a_price_above_a_loaded_cap(
    client_as, owner_a, tenant_a
):
    """S7's position for S1's editor: **no role saves a price above a loaded
    cap**, whatever produced the number."""
    item = priced(tenant_a, price="10000", cost="6000")
    cap(item, "10500")
    response = _post(
        client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "11000"}
    )
    assert response.status_code == 422
    assert "tope regulado" in response.json()["detail"]


# ---------------------------------------------------------------------------
# The one path from a suggestion to a price
# ---------------------------------------------------------------------------


def test_taking_a_suggestion_writes_one_manual_row_and_stamps_it(
    client_as, owner_a, tenant_a
):
    """Acceptance 6 · **no endpoint in this stage is called at any point.**

    Saving the suggested figure unchanged writes exactly one `item_prices` row
    at `source = manual`, carrying `proposal_id` and the person's id, and moves
    the suggestion to `taken`.
    """
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)
    proposal = live(item)

    before = ItemPrice.objects.filter(item=item).count()
    response = _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": str(proposal.suggested_price), "proposal_id": str(proposal.id)},
    )
    assert response.status_code == 200
    assert ItemPrice.objects.filter(item=item).count() == before + 1

    row = ItemPrice.objects.filter(item=item).order_by("-created_at").first()
    assert row.source == PriceSource.MANUAL
    assert row.proposal_id == proposal.id
    assert row.set_by_user_id == owner_a.id

    proposal.refresh_from_db()
    assert proposal.status == PriceProposalStatus.TAKEN
    assert proposal.resolved_price == Decimal("4700.00")
    assert proposal.suggested_price == Decimal("4700.00")
    assert proposal.resolved_by_id == owner_a.id


def test_a_different_number_is_modified_and_the_suggestion_survives_it(
    client_as, owner_a, tenant_a
):
    """Acceptance 9 · **`suggested_price` still reads what was suggested.**

    The failure to hunt for is `suggested_price` moved to match the decision --
    nothing recovers it afterwards and the adoption measurement is gone
    permanently.
    """
    settings_for(tenant_a)
    item = priced(tenant_a, price="15600", cost="13000")
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)
    proposal = live(item)
    suggested = proposal.suggested_price

    _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": "16000", "proposal_id": str(proposal.id)},
    )
    proposal.refresh_from_db()
    assert proposal.status == PriceProposalStatus.MODIFIED
    assert proposal.resolved_price == Decimal("16000.00")
    assert proposal.suggested_price == suggested


def test_dismissing_writes_no_price_row(client_as, owner_a, tenant_a):
    """*Verification* · `status = dismissed`, `resolved_by` set, `resolved_price`
    null, and **no** `item_prices` row written."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)
    proposal = live(item)
    before = ItemPrice.objects.filter(item=item).count()

    response = _post(client_as(owner_a), f"/api/price-proposals/{proposal.id}/dismiss")
    assert response.status_code == 200
    proposal.refresh_from_db()
    assert proposal.status == PriceProposalStatus.DISMISSED
    assert proposal.resolved_price is None
    assert proposal.resolved_by_id == owner_a.id
    assert ItemPrice.objects.filter(item=item).count() == before


def test_a_resolved_suggestion_is_never_touched_by_a_later_run(
    client_as, owner_a, tenant_a
):
    """*Jobs* · only rows at `proposed` or `above_cap` move to `superseded`.

    A job that rewrote a resolution would be erasing the only evidence this
    stage produces about its own worth.
    """
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    other = priced(tenant_a, price="9000", cost="7800", name="Otra referencia")
    cap(item, None, CapStatus.NOT_REGULATED)
    cap(other, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)
    proposal = live(item)
    _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": str(proposal.suggested_price), "proposal_id": str(proposal.id)},
    )

    engine.run(tenant_a.id)
    proposal.refresh_from_db()
    assert proposal.status == PriceProposalStatus.TAKEN
    assert (
        PriceProposal.objects.filter(
            item=other, status=PriceProposalStatus.SUPERSEDED
        ).count()
        == 1
    )


def test_a_price_set_with_no_proposal_in_play_still_works(client_as, owner_a, tenant_a):
    """*What this stage would break* · S1's own path, untouched: one `manual`
    row, `proposal_id` null, no `price_proposals` row moved, no error."""
    item = priced(tenant_a, price="10000", cost="6000")
    response = _post(
        client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10500"}
    )
    assert response.status_code == 200
    row = ItemPrice.objects.filter(item=item).order_by("-created_at").first()
    assert row.proposal_id is None
    assert PriceProposal.objects.count() == 0


# ---------------------------------------------------------------------------
# The cooldown and staleness
# ---------------------------------------------------------------------------


def test_a_reference_repriced_by_hand_is_not_asked_about_again(tenant_a):
    """Acceptance 31 · **whoever changed it.** Keying the cooldown on any price
    change is the correct rule now that every price change is a person's."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    cap(item, None, CapStatus.NOT_REGULATED)
    price_service.set_price(
        item=item, actor=None, tenant_id=tenant_a.id, price=Decimal("4650")
    )
    engine.run(tenant_a.id)

    assert live(item) is None
    assert estimate_for(item).no_proposal_reason == NoProposalReason.COOLDOWN


def test_an_items_first_price_row_is_not_a_change(tenant_a):
    """**The day-one trap, and it is the one this stage exists to avoid.**

    A catalog loaded this morning carries one `imported` price row per
    reference dated this morning. Reading that as a repricing would put the
    whole catalog into cooldown, and the margin rule -- the engine whose entire
    purpose is to be useful on the first morning -- would propose nothing at all
    for thirty days.
    """
    settings_for(tenant_a)
    item = make_item(tenant_a, name="Cargada hoy", presentation="caja")
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        price=Decimal("4600"),
        effective_from=timezone.localdate(),
        source=PriceSource.IMPORTED,
    )
    supplier, _ = Supplier.objects.get_or_create(tenant=tenant_a, name="Coopidrogas")
    SupplierItem.objects.create(
        tenant=tenant_a, supplier=supplier, item=item, cost=Decimal("3900")
    )
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)

    assert live(item) is not None
    assert estimate_for(item).no_proposal_reason != NoProposalReason.COOLDOWN


def test_rounding_goes_toward_the_current_price_and_never_past_the_target(tenant_a):
    """*Rounding* · **toward the current price, never away from it.**

    Rounding to whichever multiple is nearest would round a raise *up* past the
    price the arithmetic asked for -- past the goal price on a margin
    suggestion, and past the step cap on the reference the cap was set for.
    """
    del tenant_a
    # $5.000 → a $5.140 target with a 3% cap: both $5.100 and $5.150 are inside
    # the cap and $5.150 is nearer, but only $5.100 is toward the current price.
    assert engine.round_toward(
        Decimal("5140"), Decimal("5000"), Decimal("50"), Decimal("3.0")
    ) == Decimal("5100")
    # And a decrease rounds the other way, for the same reason.
    assert engine.round_toward(
        Decimal("4860"), Decimal("5000"), Decimal("50"), Decimal("3.0")
    ) == Decimal("4900")


def test_a_compliance_finding_is_raised_without_a_cost_and_survives_a_run(tenant_a):
    """The till is charging above the legal maximum today whether or not
    anybody has loaded what the box cost -- and a weekly run must not quietly
    undo the daily check that said so."""
    settings_for(tenant_a)
    item = make_item(tenant_a, name="Sin costo sobre el tope", presentation="caja")
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        price=Decimal("19400"),
        effective_from=timezone.localdate() - timedelta(days=90),
        source=PriceSource.IMPORTED,
    )
    cap(item, "18900")
    engine.run(tenant_a.id)

    proposal = live(item)
    assert proposal is not None
    assert proposal.status == PriceProposalStatus.ABOVE_CAP
    engine.run(tenant_a.id)
    assert live(item).status == PriceProposalStatus.ABOVE_CAP


def test_a_compliance_finding_is_not_a_suggestion_anybody_dismisses(
    client_as, owner_a, tenant_a
):
    """The same refusal the save path makes: dismissing it would clear the row
    off the screen and change nothing about the price the till is charging."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="19400", cost="9000")
    cap(item, "18900")
    engine.run(tenant_a.id)
    proposal = live(item)

    response = _post(client_as(owner_a), f"/api/price-proposals/{proposal.id}/dismiss")
    assert response.status_code == 422
    proposal.refresh_from_db()
    assert proposal.status == PriceProposalStatus.ABOVE_CAP


def test_a_returned_unit_is_not_demand(tenant_a):
    """*Exclusions* · a credited line is still a `sale_lines` row, and
    `sale_lines.quantity` carries a `> 0` CHECK -- so a fully refunded sale
    would otherwise enter the fit at its full quantity and its full price."""
    del tenant_a
    from core.pricing import estimator as est

    series = est.WeekSeries("item")
    week = timezone.localdate() - timedelta(days=14)
    series.units[week] = 10
    est.net_returns({"item": series}, {"item": {week: 4}})
    assert series.units[week] == 6
    # A week refunded past what it sold is a week with no demand to measure,
    # never one with negative demand: the fit takes the log of this figure.
    est.net_returns({"item": series}, {"item": {week: 99}})
    assert series.units[week] == 0


def test_a_cap_status_that_went_unknown_makes_a_suggestion_stale(
    client_as, owner_a, tenant_a
):
    """*API surface* · the other half of the third staleness rule. A reference
    stated to be outside price control carries no ceiling either, so the edit
    changes nothing about `regulated_max_price` and everything about whether
    the engine would still raise it."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)
    assert live(item).respects_regulated_cap is True

    cap(item, None, CapStatus.UNKNOWN)
    row = next(
        one
        for one in client_as(owner_a).get("/api/pricing/items").json()["rows"]
        if one["item_id"] == str(item.id)
    )
    assert row["proposal"]["stale"]["reason"] == "cap_binds"


def test_a_suggestion_goes_stale_on_the_next_read_and_nothing_is_refused(
    client_as, owner_a, tenant_a
):
    """Acceptance 24 · the collision that follows from a person being the only
    writer of `item_prices`, and it is a **rendering rule** rather than a
    refusal. Nothing is all-or-nothing any more, because nothing is a batch."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)

    price_service.set_price(
        item=item, actor=owner_a, tenant_id=tenant_a.id, price=Decimal("4800")
    )
    response = client_as(owner_a).get("/api/pricing/items?state=proposed")
    assert response.status_code == 200
    row = next(one for one in response.json()["rows"] if one["item_id"] == str(item.id))
    assert row["proposal"]["stale"]["reason"] == "price_moved"
    assert "4.800" in row["proposal"]["stale"]["detail"]


def test_a_cost_that_moved_more_than_five_per_cent_is_stale_too(
    client_as, owner_a, tenant_a
):
    """Acceptance 25 · the suggestion's whole case is a projected margin, and
    that margin is not the one the screen is showing."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)

    SupplierItem.objects.filter(item=item).update(cost=Decimal("4300"))
    response = client_as(owner_a).get("/api/pricing/items?state=proposed")
    row = next(one for one in response.json()["rows"] if one["item_id"] == str(item.id))
    assert row["proposal"]["stale"]["reason"] == "cost_moved"


# ---------------------------------------------------------------------------
# Precedence between the two engines
# ---------------------------------------------------------------------------


def test_a_qualifying_estimate_prices_the_reference_and_the_margin_rule_does_not(
    tenant_a,
):
    """Acceptance 27 · a measured response beats an assumption in **both**
    directions, including the direction where it concludes nothing is worth
    doing."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="12500", cost="8400")
    cap(item, None, CapStatus.NOT_REGULATED)
    estimate = estimator.Estimate(
        ElasticityStatus.ESTIMATED,
        elasticity=Decimal("-0.34"),
        r2=Decimal("0.41"),
        observations=22,
        confidence="high",
    )
    context = engine.Context(
        item=item,
        price=Decimal("12500"),
        cost=Decimal("8400"),
        cost_source="supplier",
        cap=None,
        cap_status=CapStatus.NOT_REGULATED,
        units=1180,
        last_change=None,
        options=pricing_settings.read(tenant_a),
        goal=Decimal("22.0"),
        today=timezone.localdate(),
    )
    suggestion = engine.decide(context, estimate)
    assert suggestion.fields["basis"] == ProposalBasis.ELASTICITY
    assert suggestion.fields["suggested_price"] == Decimal("12850")
    assert suggestion.fields["reason_code"] == "inelastic_raise"
    # The stage document's own worked example: +$364.050 a month, on eleven
    # fewer units.
    assert (
        Decimal("360000")
        < suggestion.fields["estimated_monthly_impact"]
        < Decimal("370000")
    )


def test_a_weak_elastic_reading_vetoes_both_engines(tenant_a):
    """Acceptance 28 · **weak evidence may stop a move and may not start one.**"""
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    cap(item, None, CapStatus.NOT_REGULATED)
    estimate = estimator.Estimate(
        ElasticityStatus.ESTIMATED,
        elasticity=Decimal("-1.42"),
        r2=Decimal("0.21"),
        observations=13,
        confidence="low",
    )
    context = engine.Context(
        item=item,
        price=Decimal("4600"),
        cost=Decimal("3900"),
        cost_source="supplier",
        cap=None,
        cap_status=CapStatus.NOT_REGULATED,
        units=100,
        last_change=None,
        options=pricing_settings.read(tenant_a),
        goal=Decimal("22.0"),
        today=timezone.localdate(),
    )
    suggestion = engine.decide(context, estimate)
    assert suggestion.fields is None
    assert suggestion.no_proposal_reason == NoProposalReason.ELASTIC_VETO


# ---------------------------------------------------------------------------
# The API surface, its roles and its settings group
# ---------------------------------------------------------------------------


def test_a_cashier_reaches_no_pricing_route(client_as, cashier_a):
    """Acceptance 12 · a denial naming the role required, not a redirect."""
    for path in (
        "/api/pricing/items",
        "/api/pricing/summary",
        "/api/pricing/adoption",
        "/api/pricing/caps",
        "/api/settings/pricing",
    ):
        response = client_as(cashier_a).get(path)
        assert response.status_code == 403, path
        assert "perfil" in response.json()["detail"]


def test_only_an_owner_triggers_a_run_or_writes_the_group(client_as, admin_a, owner_a):
    """*API surface* · both office roles read every surface and both may load
    caps; only an `owner` runs the engine or moves a setting."""
    assert client_as(admin_a).get("/api/pricing/summary").status_code == 200
    assert _post(client_as(admin_a), "/api/pricing/runs").status_code == 403
    assert (
        client_as(admin_a)
        .patch(
            "/api/settings/pricing",
            data=json.dumps({"max_single_step_pct": 4.0}),
            content_type="application/json",
        )
        .status_code
        == 403
    )
    assert (
        client_as(owner_a)
        .patch(
            "/api/settings/pricing",
            data=json.dumps({"max_single_step_pct": 4.0}),
            content_type="application/json",
        )
        .status_code
        == 200
    )


def test_the_settings_write_leaves_every_other_group_standing(
    client_as, owner_a, tenant_a
):
    """*What this stage would break* · one `jsonb_set`, every other group
    intact (ledger rule 5)."""
    from core import tenant_settings

    tenant_settings.write_group(tenant_a, "purchasing", {"refresh_hour": 4})
    client_as(owner_a).patch(
        "/api/settings/pricing",
        data=json.dumps({"margin_goal_pct": 26.0}),
        content_type="application/json",
    )
    tenant_a.refresh_from_db()
    assert tenant_a.settings["purchasing"] == {"refresh_hour": 4}
    assert tenant_a.settings["pricing"]["margin_goal_pct"] == 26.0
    assert AuditLog.objects.filter(entity_type="settings.pricing").count() == 1


def test_clearing_the_margin_goal_is_a_legal_write(client_as, owner_a, tenant_a):
    """It is how a tenant returns to the first-morning state, reached through the
    surface that owns the setting rather than by editing a row."""
    settings_for(tenant_a)
    response = client_as(owner_a).patch(
        "/api/settings/pricing",
        data=json.dumps({"clear_margin_goal": True}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["margin_goal_pct"] is None


def test_a_cap_edit_writes_no_price_and_lands_in_the_audit_log(
    client_as, owner_a, tenant_a
):
    """Acceptance 19 · and *Data* · a cap is a **constraint**, not a price: it
    opens no price window and closes none."""
    item = priced(tenant_a, price="10000", cost="6000")
    before = ItemPrice.objects.filter(item=item).count()
    response = _put(
        client_as(owner_a),
        f"/api/pricing/caps/{item.id}",
        {
            "cap_status": "capped",
            "regulated_max_price": "18900",
            "source": "Circular 1",
        },
    )
    assert response.status_code == 200
    item.refresh_from_db()
    assert item.regulated_max_price == Decimal("18900.00")
    assert item.cap_status == CapStatus.CAPPED
    assert item.custom["pricing"]["cap_status"] == CapStatus.CAPPED
    assert ItemPrice.objects.filter(item=item).count() == before
    assert AuditLog.objects.filter(entity_type="items.regulated_max_price").count() == 1


def test_a_cap_import_reads_a_spreadsheet_and_records_one_audit_row(
    client_as, owner_a, tenant_a
):
    """*API surface* · synchronous, refused above 5.000 rows, and it writes **no
    `imports` row**: that table is S1's and S6's."""
    item = priced(tenant_a, price="10000", cost="6000")
    item.external_code = "LEG-00042"
    item.save(update_fields=["external_code"])
    payload = (
        "codigo,precio_maximo,fuente\nLEG-00042,18.900,Circular 07\nLEG-99999,1.000,x\n"
    )
    response = _post(client_as(owner_a), "/api/pricing/caps/import", {"csv": payload})
    assert response.status_code == 200
    body = response.json()
    assert body["loaded"] == 1
    assert body["unmatched"][0]["code"] == "LEG-99999"
    item.refresh_from_db()
    assert item.regulated_max_price == Decimal("18900.00")
    from core.models import ImportRun

    assert ImportRun.objects.count() == 0


def test_the_grid_distinguishes_could_not_look_from_nothing_to_do(
    client_as, owner_a, tenant_a
):
    """Acceptance 21 · **a build that shows the same badge for the first and the
    same sentence for the last two fails this criterion.**"""
    settings_for(tenant_a)
    no_cost = make_item(tenant_a, name="Sin costo", presentation="caja")
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=no_cost,
        price=Decimal("5000"),
        effective_from=timezone.localdate() - timedelta(days=40),
        source=PriceSource.IMPORTED,
    )
    at_goal = priced(tenant_a, price="10000", cost="7590", name="En la meta")
    cap(at_goal, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)

    rows = {
        one["item_id"]: one
        for one in client_as(owner_a).get("/api/pricing/items").json()["rows"]
    }
    assert rows[str(no_cost.id)]["state"] == "unevaluated"
    assert "Sin costo cargado" in rows[str(no_cost.id)]["reason"]
    assert rows[str(at_goal.id)]["state"] == "no_proposal"
    assert "por encima de la meta" in rows[str(at_goal.id)]["reason"]


def test_the_summary_and_the_estimate_table_report_the_same_evaluated_count(
    client_as, owner_a, tenant_a
):
    """*Verification* · **the check is that the three agree**, not that they
    reach a number this document names."""
    settings_for(tenant_a)
    for index in range(4):
        priced(tenant_a, price="9000", cost="7000", name=f"Referencia {index}")
    engine.run(tenant_a.id)

    body = client_as(owner_a).get("/api/pricing/summary").json()
    latest = (
        ElasticityEstimate.objects.filter(tenant=tenant_a)
        .order_by("-computed_at")
        .values_list("computed_at", flat=True)
        .first()
    )
    assert (
        body["evaluated"]
        == ElasticityEstimate.objects.filter(computed_at=latest).count()
    )
    assert sum(body["by_status"].values()) == body["evaluated"]


def test_adoption_is_split_by_basis_and_never_aggregated(client_as, owner_a, tenant_a):
    """Acceptance 32 · **no figure on that panel aggregates the two engines**,
    and every share carries its denominator."""
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    cap(item, None, CapStatus.NOT_REGULATED)
    engine.run(tenant_a.id)
    proposal = live(item)
    _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": str(proposal.suggested_price), "proposal_id": str(proposal.id)},
    )

    body = client_as(owner_a).get("/api/pricing/adoption").json()
    assert [one["basis"] for one in body["by_basis"]] == ["margin_rule", "elasticity"]
    margin = next(one for one in body["by_basis"] if one["basis"] == "margin_rule")
    assert margin["taken"] == 1
    assert margin["resolved"] == 1


def test_two_runs_in_one_morning_produce_one_run(tenant_a):
    """Acceptance 14 · the job's idempotency key, read off the rows a run wrote,
    because there is no run table."""
    from core.pricing import jobs

    settings_for(tenant_a)
    priced(tenant_a, price="4600", cost="3900")
    jobs.pricing_run(tenant_id=str(tenant_a.id), window_end=timezone.localdate())
    stamps = set(ElasticityEstimate.objects.values_list("computed_at", flat=True))
    answer = jobs.pricing_run(
        tenant_id=str(tenant_a.id), window_end=timezone.localdate()
    )
    assert answer["skipped"] is True
    assert (
        set(ElasticityEstimate.objects.values_list("computed_at", flat=True)) == stamps
    )


def test_a_run_never_no_ops_for_want_of_sales(tenant_a):
    """Acceptance 26 · **the day-one path is the ordinary path with one engine
    silent** -- not a special case and not a separate command."""
    settings_for(tenant_a)
    for index in range(3):
        item = priced(tenant_a, price="4600", cost="3900", name=f"Ref {index}")
        cap(item, None, CapStatus.NOT_REGULATED)
    report = engine.run(tenant_a.id)

    assert report["proposals"] == 3
    assert report["margin_rule"] == 3
    assert report["elasticity"] == 0
    assert (
        ElasticityEstimate.objects.filter(status=ElasticityStatus.NO_SALES).count() == 3
    )
    assert (
        PriceProposal.objects.filter(estimated_monthly_impact__isnull=False).count()
        == 0
    )


def test_the_stage_writes_its_own_key_of_items_custom_and_no_other(tenant_a):
    """*What this stage would break* · every key but `pricing` byte-identical.

    A whole-document write passes every check in this stage and silently deletes
    another stage's key.
    """
    settings_for(tenant_a)
    item = priced(tenant_a, price="4600", cost="3900")
    item.custom = {"catalog": {"shelf": "A3"}}
    item.save(update_fields=["custom"])
    engine.run(tenant_a.id)

    item.refresh_from_db()
    assert item.custom["catalog"] == {"shelf": "A3"}
    assert item.custom["pricing"]["elasticity_band"] == "unknown"


def test_both_new_tables_carry_a_policy_and_force_row_level_security():
    """*Gates* · a table that has already reached a second tenant cannot be
    repaired by an edit, which is why this is a gate rather than a check."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('elasticity_estimates', 'price_proposals')"
        )
        rows = {name: (enabled, forced) for name, enabled, forced in cursor.fetchall()}
    assert rows == {
        "elasticity_estimates": (True, True),
        "price_proposals": (True, True),
    }


def test_a_second_tenant_reads_none_of_the_first_tenants_rows(
    tenant_a, tenant_b, as_runtime_role
):
    """A1 · the regression no screen ever reveals and no later session finds by
    looking."""
    from core.tenancy import pin_tenant

    settings_for(tenant_a)
    priced(tenant_a, price="4600", cost="3900")
    engine.run(tenant_a.id)

    with pin_tenant(tenant_b.id):
        as_runtime_role()
        assert PriceProposal.objects.count() == 0
        assert ElasticityEstimate.objects.count() == 0
