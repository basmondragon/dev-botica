"""The demo seed's assistant fixture — **this stage's completion test.**

*Seed fixtures* is emphatic that this stage is not finished until the surfaces
render convincingly from the seed, which is a sharper test than the suite:
the suite never opens card C on a sede with no rules.

Each check seeds a whole tenant, which is slow on purpose: it is the same
command a person runs before a demo, and a fixture that only worked inside a
test is a fixture nobody can show.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db.models import Count
from django.utils import timezone

from core.assistant import (
    demo as assistant_demo,
    extract,
    filters,
    pipeline,
    service,
)
from core.assistant import settings as assistant_settings
from core.assistant.vocabulary import check_triggers
from core.demo import identity, registry as demo_registry
from core.models import (
    AssistantMode,
    AssistantQuery,
    AssistantSuggestion,
    CrossSellBasis,
    CrossSellRule,
    ItemWarning,
    Location,
    SaleLine,
    StockOnHand,
    SuggestionType,
    Tenant,
)
from core.tenancy import pin_tenant

pytestmark = pytest.mark.django_db(transaction=True)

HANDOFF = "Lleva dos días con diarrea y algo de fiebre. Adulto, toma losartán."
HOT = "Lleva dos días con diarrea y fiebre de 39. Adulto, toma losartán."


def seed(profile):
    call_command("seed_demo_tenant", profile=profile)
    return demo_registry.uid(profile, "tenants", identity.slug_for(profile))


def period():
    closed = timezone.now() + timedelta(days=1)
    return closed - timedelta(days=400), closed


def card_c(tenant_id, code, transcript):
    with pin_tenant(tenant_id):
        values = assistant_settings.read(Tenant.objects.get(id=tenant_id))
        location = Location.objects.get(tenant_id=tenant_id, code=code)
        return pipeline.run(
            tenant_id=tenant_id,
            location_id=location.id,
            symptoms=extract.extract(transcript),
            settings=values,
        )


def test_the_default_seed_draws_the_screens_this_stage_draws():
    """Acceptance 2, 5, 28 and check 1, 14 · **the whole of card C, from the
    seed.**

    One seeded tenant answers three questions at once, and it is one run rather
    than three because a full network takes the better part of a minute to build
    and the suite runs it on every change: Chapinero's three cards, Usme's two
    with nothing in the third slot, and the handoff's own loperamida row
    disappearing when a temperature is given.
    """
    tenant_id = seed("default")

    # 1 · Chapinero draws three cards with three different type pills.
    warm = card_c(tenant_id, "CHA", HANDOFF)
    assert [card["type"] for card in warm.cards] == [
        SuggestionType.FIRST_CHOICE,
        SuggestionType.CONDITIONAL,
        SuggestionType.BOUGHT_TOGETHER,
    ]
    for card in warm.cards:
        assert card["available_quantity"] > 0
        assert card["reason"].strip()
        assert card["price"] > 0
    assert warm.candidate_count > len(warm.cards)
    with pin_tenant(tenant_id):
        chapinero = Location.objects.get(tenant_id=tenant_id, code="CHA")
        for card in warm.cards:
            held = StockOnHand.objects.filter(
                tenant_id=tenant_id, location=chapinero, item_id=card["item_id"]
            )
            assert sum(one.quantity for one in held) >= card["available_quantity"]

    # 2 · **Nothing occupies the third slot** at the cold sede. A new sede's
    # normal first state, and not an error state (§1).
    cold = card_c(tenant_id, "USM", HANDOFF)
    assert [card["type"] for card in cold.cards] == [
        SuggestionType.FIRST_CHOICE,
        SuggestionType.CONDITIONAL,
    ]
    assert cold.candidate_count > 0

    # 3 · A temperature removes the card entirely and **both figures fall by
    # exactly one**. *Wrong* is the card present carrying the warning as its
    # text — that is a filter that ran after ranking (§7).
    conditional = next(
        card for card in warm.cards if card["type"] == SuggestionType.CONDITIONAL
    )
    assert conditional["reason"] == assistant_demo.LOPERAMIDA_TEXT
    hot = card_c(tenant_id, "CHA", HOT)
    assert hot.candidate_count == warm.candidate_count - 1
    assert len(hot.cards) == len(warm.cards) - 1
    assert conditional["item_id"] not in {card["item_id"] for card in hot.cards}
    removed = [one for one in hot.excluded if one["reason"] == "warning_blocking"]
    assert len(removed) == 1
    assert removed[0]["item_id"] == conditional["item_id"]
    assert removed[0]["warning_id"]


def test_the_measurement_contract_holds_three_ways_on_one_seed():
    """Checks 2, 9, 10, 11 and 12 · **the Panel's tile rests entirely on this.**

    One seeded tenant, five questions: what may never be offered, the rate read
    three ways, one line credited once, the population predicate, and the two
    figures S9 draws.
    """
    tenant_id = seed("default")
    opened, closed = period()

    with pin_tenant(tenant_id):
        # Check 2 · counted **across every seeded query**, not on one transcript,
        # and against the denominator the product itself reports.
        offers = AssistantSuggestion.objects.filter(tenant_id=tenant_id)
        assert offers.count() > 0
        assert offers.filter(item__requires_prescription=True).count() == 0
        assert offers.filter(item__controlled=True).count() == 0
        assert offers.filter(item__invima_status="expired").count() == 0

        # **And no seeded card is a product the filter would have removed.** A
        # fixture that credited one would seed exactly the state check 1 calls
        # *Wrong* -- a card present carrying a satisfied blocking warning as its
        # own text -- and the seed would keep passing while the filter was
        # broken.
        blocking = {
            str(row.item_id): row
            for row in ItemWarning.objects.filter(
                tenant_id=tenant_id, active=True, severity="blocking"
            )
        }
        for row in offers.select_related("query")[:4000]:
            warning = blocking.get(str(row.item_id))
            if warning is None:
                continue
            outcome = filters.evaluate(
                warning.triggers, filters.Extraction(row.query.symptoms)
            )
            assert outcome != filters.SATISFIED, row.item_id

        # Check 9 · **the check is that all three are the same figure.** A
        # disagreement means somebody recomputed a definition instead of reading
        # it.
        rows = service.offered_population(tenant_id, opened=opened, closed=closed)
        counted = (rows.count(), rows.filter(accepted=True).count())
        figures = service.acceptance(tenant_id, opened=opened, closed=closed)
        assert counted == (figures["offered"], figures["accepted"])
        assert figures["rate"] == counted[1] / counted[0]
        assert counted == (assistant_demo.OFFERED, assistant_demo.ACCEPTED)
        assert round(figures["rate"] * 100, 1) == 58.6

        # Check 10 · anything else and the numerator is inflated (A5).
        flagged = SaleLine.objects.filter(
            tenant_id=tenant_id, from_suggestion=True
        ).count()
        credited = AssistantSuggestion.objects.filter(
            tenant_id=tenant_id, sale_line__isnull=False
        )
        assert flagged == credited.count() == assistant_demo.ACCEPTED
        doubled = [
            row
            for row in credited.values("sale_line_id").annotate(rows=Count("id"))
            if row["rows"] > 1
        ]
        assert doubled == []

        # Check 11 · the seed holds no imported sale at all, so demanding the
        # rate *move* would assert something a correct implementation cannot
        # produce. **Assert the filter instead.**
        predicate = str(rows.query)
        assert "source" in predicate and "counter" in predicate

        # Check 12 · **the two figures are the fixture's targets and not
        # thresholds**; the assertion is the separation and the direction.
        comparison = service.ticket_comparison(tenant_id, opened=opened, closed=closed)
        assert comparison["suggested_tickets"] > 0
        assert comparison["plain_tickets"] > 0
        assert comparison["suggested_mean"] > comparison["plain_mean"] * Decimal("1.3")

        # Check 12's second half · at category grain, directional, and stable
        # across two runs over one period.
        first = service.combinations(tenant_id, opened=opened, closed=closed)
        again = service.combinations(tenant_id, opened=opened, closed=closed)
        assert len(first) >= 3
        assert [one["label"] for one in first] == [one["label"] for one in again]
        for row in first:
            assert row["anchor"] and row["suggestion"]
            assert row["anchor"] != row["suggestion"]
            assert row["sales"] > 0


def test_the_rules_are_mined_and_the_query_log_is_populated():
    """The gate and check 14 · a seeded rule table hides the one failure this
    seed exists to catch, and **wrong** is any zero denominator or any column
    that is `—` all the way down."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        rules = CrossSellRule.objects.filter(tenant_id=tenant_id)
        assert rules.count() > 0
        # Mined from Botica's own trading, and the provenance says so.
        assert set(rules.values_list("basis", flat=True)) == {CrossSellBasis.COUNTER}
        assert all(one > 0 for one in rules.values_list("ticket_count", flat=True))
        assert rules.filter(location__isnull=True).exists()
        assert rules.filter(location__isnull=False).exists()

        # The gate · **every trigger on every seeded row is inside the shipped
        # closed vocabulary.** A trigger naming a key the extractor cannot emit
        # never fires and nothing anywhere raises, so the safety layer is
        # decorative — which is why this is checked on the data and not only on
        # the endpoint that refuses a bad write.
        for warning in ItemWarning.objects.filter(tenant_id=tenant_id):
            assert check_triggers(warning.triggers) == warning.triggers

        rows = AssistantQuery.objects.filter(tenant_id=tenant_id)
        assert rows.filter(mode=AssistantMode.MODEL).exists()
        assert rows.filter(mode=AssistantMode.LOCAL).exists()
        assert rows.filter(output_check_passed=False).exists()
        assert rows.exclude(cost_usd=0).exists()
        assert rows.exclude(latency_ms=None).exists()
        assert rows.exclude(transcript="").exists()
        # Six sedes, or the Panel reads as a mock of itself.
        per_sede = rows.values("location_id").annotate(n=Count("id"))
        assert len(per_sede) == 6


def test_the_cold_profile_mines_nothing_and_still_has_a_safety_layer():
    """The gate and check 6's second pass · **a run that finds nothing above the
    floor is a pass, not a failure**, and the map and the warnings are
    configuration rather than history, so they are seeded under every profile."""
    tenant_id = seed("cold")
    with pin_tenant(tenant_id):
        assert CrossSellRule.objects.filter(tenant_id=tenant_id).count() == 0
        assert AssistantQuery.objects.filter(tenant_id=tenant_id).count() == 0
        assert AssistantSuggestion.objects.filter(tenant_id=tenant_id).count() == 0
        assert ItemWarning.objects.filter(tenant_id=tenant_id).count() > 0
        values = assistant_settings.read(Tenant.objects.get(id=tenant_id))
        assert values["symptom_category_map"]


def test_the_young_profile_is_all_learning_band():
    """`young` · twelve days, so **every rule that clears the floor is `low`**
    and the figureless form of the reason line renders everywhere."""
    tenant_id = seed("young")
    with pin_tenant(tenant_id):
        bands = set(
            CrossSellRule.objects.filter(tenant_id=tenant_id).values_list(
                "confidence_band", flat=True
            )
        )
        assert bands <= {"low"}
        assert AssistantSuggestion.objects.filter(tenant_id=tenant_id).exists()


def test_running_the_seed_twice_produces_the_same_tenant():
    """The gate · a fixture that is order-dependent or picks a random value
    fails here rather than in a demo.

    It runs on `young` rather than on `default` for one reason: it is the only
    check in this file that has to build a tenant **twice**, and `young` is the
    same fixture over twelve days. Determinism is a property of the code and not
    of the window.
    """
    first = seed("young")
    with pin_tenant(first):
        before = (
            sorted(
                ItemWarning.objects.filter(tenant_id=first).values_list("id", flat=True)
            ),
            AssistantSuggestion.objects.filter(tenant_id=first).count(),
            sorted(
                str(one)
                for one in AssistantQuery.objects.filter(tenant_id=first).values_list(
                    "id", flat=True
                )
            ),
        )
    again = seed("young")
    assert again == first
    with pin_tenant(again):
        after = (
            sorted(
                ItemWarning.objects.filter(tenant_id=again).values_list("id", flat=True)
            ),
            AssistantSuggestion.objects.filter(tenant_id=again).count(),
            sorted(
                str(one)
                for one in AssistantQuery.objects.filter(tenant_id=again).values_list(
                    "id", flat=True
                )
            ),
        )
    assert before == after
