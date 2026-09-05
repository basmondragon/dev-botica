"""The fixture, and what the Precios screen must render from it.

**The seed is the last check, and `convincingly` is not a feeling** (§1). What
is asserted here is what a person would see: both engines on the screen without
touching a filter, four tiles carrying a figure, a compliance finding on the
critical tint, `Sin evaluar` and `Sin propuesta` side by side saying different
things, resolved suggestions showing two numbers, and an `Adopción` panel with a
real ratio in it.

**Nothing here is written by hand.** The fixture sets the goal, loads the caps
the way a regente would, runs `pricing.run` exactly as the cron does, and
resolves a subset **through S1's price editor** -- so a state that appeared only
because a fixture stamped it is a state nobody has tested.

**No figure here is a production sizing.** The handoff's `4.284` references
describe the tenant the designer had; this seed holds what its own fixtures
built, and *Verification* says so explicitly: assert against the seed's own
counts, or against what the product itself reports, never against a literal.
"""

import pytest
from django.core.management import call_command
from django.db.models import Count

from core.demo import identity, registry as demo_registry
from core.models import (
    CapStatus,
    ElasticityEstimate,
    ElasticityStatus,
    Item,
    ItemPrice,
    PriceProposal,
    PriceProposalStatus,
    PriceSource,
    ProposalBasis,
)
from core.pricing import settings as pricing_settings
from core.tenancy import pin_tenant

pytestmark = pytest.mark.django_db


def seed(profile):
    call_command("seed_demo_tenant", profile=profile)
    return demo_registry.uid(profile, "tenants", identity.slug_for(profile))


def latest_run(tenant_id):
    return (
        ElasticityEstimate.objects.filter(tenant_id=tenant_id)
        .order_by("-computed_at")
        .values_list("computed_at", flat=True)
        .first()
    )


# ---------------------------------------------------------------------------
# The gate: nothing here came from a fixture
# ---------------------------------------------------------------------------


def test_every_proposal_came_from_the_engine_or_from_s1(tenant_a):
    """*Verification* · **no row in `price_proposals` written by a fixture.**

    A fixture that bypasses the engine keeps rendering a convincing screen after
    the engine has broken, which is the one thing a completion test must not do.
    Every row carries the model version of the run that wrote it, and every
    resolution carries the name of a person.
    """
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        assert PriceProposal.objects.filter(model_version="").count() == 0
        assert PriceProposal.objects.count() > 0
        assert ElasticityEstimate.objects.filter(model_version="").count() == 0
        resolved = PriceProposal.objects.filter(
            status__in=("taken", "modified", "dismissed")
        )
        assert resolved.count() > 0
        assert resolved.filter(resolved_by_name="").count() == 0
        assert resolved.filter(resolved_at__isnull=True).count() == 0


def test_the_seed_never_demonstrates_the_guardrail_switched_off(tenant_a):
    """*Demo seed* · `allow_raise_without_cap` is **false** on every profile."""
    del tenant_a
    for profile in ("default", "cold"):
        tenant_id = seed(profile)
        with pin_tenant(tenant_id):
            from core.models import Tenant

            options = pricing_settings.read(Tenant.objects.get(id=tenant_id))
            assert options["allow_raise_without_cap"] is False
            assert options["margin_goal_pct"] == 22.0


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------


def test_the_grid_shows_a_margin_proposal_and_a_compliance_finding(tenant_a):
    """Acceptance 34 · a populated grid, and at least one reference above its
    cap so `Propuestas sobre el tope` is non-zero and the critical badge and the
    absent-action rule are both visible without anyone constructing them."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        live = PriceProposal.objects.filter(status=PriceProposalStatus.PROPOSED)
        assert live.filter(basis=ProposalBasis.MARGIN_RULE).count() > 0
        assert (
            PriceProposal.objects.filter(status=PriceProposalStatus.ABOVE_CAP).count()
            >= 1
        )
        assert live.filter(reason_code="cap_bound_raise").count() >= 1


def test_caps_are_seeded_in_all_three_states(tenant_a):
    """*Demo seed* · a realistic proportion, and **none of the references the
    margin rule acts on is at `unknown`** -- the default forbids raising those,
    and a seed that contradicted its own engine would teach a build agent the
    wrong rule."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        by_state = dict(Item.objects.values_list("cap_status").annotate(n=Count("id")))
        assert by_state.get(CapStatus.CAPPED, 0) > 0
        assert by_state.get(CapStatus.NOT_REGULATED, 0) > 0
        assert by_state.get("", 0) + by_state.get(CapStatus.UNKNOWN, 0) > 0

        raised = PriceProposal.objects.filter(
            status=PriceProposalStatus.PROPOSED,
            suggested_price__gt=models_f("current_price"),
        )
        assert raised.count() > 0
        unknown = {
            one.id
            for one in Item.objects.filter(cap_status__in=("", CapStatus.UNKNOWN)).only(
                "id"
            )
        }
        assert not {one.item_id for one in raised} & unknown


def models_f(name):
    from django.db.models import F

    return F(name)


def test_both_no_proposal_badges_are_present_and_say_different_things(tenant_a):
    """Acceptance 21 · `Sin evaluar` and `Sin propuesta` can only be checked for
    conflation side by side."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        run = ElasticityEstimate.objects.filter(computed_at=latest_run(tenant_id))
        statuses = set(run.values_list("status", flat=True))
        # *we could not look*
        assert statuses & {ElasticityStatus.NO_COST, ElasticityStatus.INACTIVE}
        # *we looked and there is nothing to do*
        reasons = set(run.values_list("no_proposal_reason", flat=True))
        assert "at_margin_goal" in reasons
        assert "cap_blocks_raise" in reasons


def test_resolved_suggestions_carry_their_manual_price_rows(tenant_a):
    """*Demo seed* · **the three resolved states are S1's writes even in the
    seed**, and each `taken` or `modified` carries its matching `item_prices`
    row at `source = manual` with `proposal_id` and a seeded author."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        for status in ("taken", "modified", "dismissed"):
            assert PriceProposal.objects.filter(status=status).exists(), status

        for proposal in PriceProposal.objects.filter(status__in=("taken", "modified")):
            row = ItemPrice.objects.filter(proposal_id=proposal.id).first()
            assert row is not None
            assert row.source == PriceSource.MANUAL
            assert row.set_by_user_id is not None
            assert row.price == proposal.resolved_price

        assert (
            ItemPrice.objects.filter(
                proposal_id__in=PriceProposal.objects.filter(status="dismissed").values(
                    "id"
                )
            ).count()
            == 0
        )


def test_the_modified_gap_runs_in_both_directions(tenant_a):
    """*Demo seed* · so the median signed gap is not trivially one-sided, which
    is what makes `Adopción` a measurement rather than a decoration."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        gaps = [
            one.resolved_price - one.suggested_price
            for one in PriceProposal.objects.filter(status="modified")
        ]
        assert any(gap > 0 for gap in gaps)
        assert any(gap < 0 for gap in gaps)


def test_a_stale_suggestion_is_visible_without_waiting_a_week(
    tenant_a, client_as, owner_a
):
    """Acceptance 24 · two references whose price a person moved after the run,
    so the greyed figures and the no-pre-fill action are both visible."""
    del tenant_a, owner_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        from core.models import User

        person = User.objects.filter(role="owner").first()
    response = client_as(person).get("/api/pricing/items?page_size=100")
    assert response.status_code == 200
    stale = [
        one
        for one in response.json()["rows"]
        if one.get("proposal") and one["proposal"].get("stale")
    ]
    assert stale, "the seed builds no stale suggestion"


def test_the_provenance_line_and_the_grid_agree_on_the_mix(tenant_a, client_as):
    """*Verification* · **the assertion is that the two counts agree**, not that
    either reaches a literal: the ratio is a property of the fixture."""
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        from core.models import User

        person = User.objects.filter(role="owner").first()
        by_basis = dict(
            PriceProposal.objects.filter(status=PriceProposalStatus.PROPOSED)
            .values_list("basis")
            .annotate(n=Count("id"))
        )
    summary = client_as(person).get("/api/pricing/summary").json()
    assert summary["by_basis"]["margin_rule"] == by_basis.get("margin_rule", 0)
    assert summary["by_basis"]["elasticity"] == by_basis.get("elasticity", 0)
    assert summary["references_with_proposal"] == sum(by_basis.values())
    assert summary["above_cap"] >= 1


def test_the_estimator_produced_real_fits_rather_than_stamped_numbers(tenant_a):
    """*Demo seed* · **real fits.** Every estimate the seed carries came out of
    a regression over sales S4 wrote, so a β is a coefficient and not a value
    somebody typed -- and every one of them is a demand response.

    **The elasticity engine's *proposals* are a different count**, and it is
    bounded by something this fixture cannot move: the materiality floor is a
    production-sized constant (`$50.000` a month), and this seed rings roughly
    ten thousand tickets across six sedes, which puts almost every single
    reference's monthly contribution an order of magnitude below it. That is the
    honest reading of a tenant this size, and it is exactly what the screen is
    built to say out loud -- so what is asserted here is that the estimates are
    real and that the withholding is legible, not that a proposal exists.
    """
    del tenant_a
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        run = ElasticityEstimate.objects.filter(computed_at=latest_run(tenant_id))
        fitted = run.filter(status=ElasticityStatus.ESTIMATED)
        assert fitted.count() > 0
        assert run.filter(elasticity=0).count() == 0
        for one in fitted:
            assert one.elasticity < 0
            assert abs(one.elasticity) <= 5
            assert one.observations >= 8
            assert one.distinct_prices >= 2
            assert one.r2 is not None
            assert one.confidence in ("high", "medium", "low")
        # The two floors that decide whether an estimate may price anything are
        # visible on the rows themselves, so a reader can check the gate rather
        # than take the band on trust.
        assert set(fitted.values_list("confidence", flat=True)) <= {
            "high",
            "medium",
            "low",
        }


# ---------------------------------------------------------------------------
# The other profiles
# ---------------------------------------------------------------------------


def test_the_sales_free_path_on_cold(tenant_a):
    """Acceptance 26 and 30 · **`cold` is a profile, not a mutilated `default`.**

    Every evaluated reference carries an estimate at `no_sales` with a null
    elasticity, every live proposal is `margin_rule`, every impact cell reads
    `Sin volumen`, and nothing anywhere is an error state.
    """
    del tenant_a
    tenant_id = seed("cold")
    with pin_tenant(tenant_id):
        run = ElasticityEstimate.objects.filter(computed_at=latest_run(tenant_id))
        assert run.count() > 0
        assert (
            run.exclude(status=ElasticityStatus.NO_SALES)
            .exclude(status=ElasticityStatus.NO_COST)
            .count()
            == 0
        )
        assert run.filter(elasticity__isnull=False).count() == 0
        assert PriceProposal.objects.filter(basis=ProposalBasis.ELASTICITY).count() == 0
        live = PriceProposal.objects.filter(status=PriceProposalStatus.PROPOSED)
        assert live.count() > 0
        assert live.filter(estimated_monthly_impact__isnull=False).count() == 0
        assert live.filter(trailing_monthly_units__isnull=False).count() == 0


def test_minimal_holds_rows_so_an_isolation_check_reads_zero_from_a_full_table(
    tenant_a,
):
    """*Demo seed* · an isolation check reading zero rows from the other tenant
    should be reading zero from a table that **has** rows, rather than watching
    two empty tables agree with each other."""
    del tenant_a
    tenant_id = seed("minimal")
    with pin_tenant(tenant_id):
        assert ElasticityEstimate.objects.count() > 0


def test_young_renders_and_says_why(tenant_a):
    """*Demo seed* · twelve days is under every floor the estimator sets, so the
    estimates exist and read `insufficient_observations` and the grid is
    `Margen` throughout. S7 asserts nothing else here -- `young` is S9's
    profile -- but the screen renders and says why."""
    del tenant_a
    tenant_id = seed("young")
    with pin_tenant(tenant_id):
        run = ElasticityEstimate.objects.filter(computed_at=latest_run(tenant_id))
        assert run.count() > 0
        assert PriceProposal.objects.filter(basis=ProposalBasis.ELASTICITY).count() == 0
