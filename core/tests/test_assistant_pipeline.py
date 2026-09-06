"""The five steps, and the one that decides whether a product reaches a card.

**Every check here fails on a query result rather than on an opinion.** The
safety layer is a filter and never an annotation (§7, A8), so what is asserted
is that a blocking warning removes the product, that `excluded` names the row
that removed it, and that there is no path from a filtered item to a rendered
card.
"""

from decimal import Decimal

import pytest

from core.assistant import extract, filters, pipeline
from core.assistant import settings as assistant_settings
from core.assistant.vocabulary import InvalidTrigger, check_triggers
from core.models import (
    Category,
    ItemWarning,
    ItemWarningSource,
    ItemWarningType,
    SuggestionType,
    Tenant,
    WarningSeverity,
)
from core.tenancy import pin_tenant
from core.tests.test_counter_push import price, stock
from core.tests.test_inventory_ledger import make_lot
from core.tests.test_sync_pull import make_item

pytestmark = pytest.mark.django_db

HANDOFF = "Lleva dos días con diarrea y algo de fiebre. Adulto, toma losartán."
HOT = "Lleva dos días con diarrea y fiebre de 39. Adulto, toma losartán."

LOPERAMIDA_TEXT = "no ofrecer si la fiebre pasa de 38,5 °C o si hay sangre"
LOPERAMIDA_TRIGGERS = [
    {"symptom": "fever", "operator": ">", "value": 38.5, "unit": "celsius"},
    {"symptom": "blood_in_stool"},
]


def shelf(tenant, location):
    """A small Digestivo shelf with the handoff's own two references on it."""
    digestive = Category.objects.create(tenant=tenant, name="Digestivo")
    salts = make_item(tenant, "Sales de rehidratación oral", category=digestive)
    loperamide = make_item(
        tenant,
        "Loperamida 2 mg × 12",
        category=digestive,
        active_ingredient="Loperamida",
    )
    for item, amount in ((salts, "3900"), (loperamide, "8400")):
        price(tenant, item, amount)
        stock(
            tenant,
            location,
            item,
            make_lot(tenant, item, code=f"L-{item.name[:4]}"),
            20,
        )
    return digestive, salts, loperamide


def configure(tenant, digestive, **extra):
    with pin_tenant(tenant.id):
        return assistant_settings.write(
            Tenant.objects.get(id=tenant.id),
            {
                "symptom_category_map": {
                    "diarrhea": [str(digestive.id)],
                    "fever": [str(digestive.id)],
                },
                **extra,
            },
        )


def warn(
    tenant,
    item,
    *,
    text,
    triggers,
    severity=WarningSeverity.BLOCKING,
    kind=ItemWarningType.DO_NOT_SUGGEST_IF,
):
    return ItemWarning.objects.create(
        tenant=tenant,
        item=item,
        type=kind,
        text=text,
        severity=severity,
        source=ItemWarningSource.CATALOG,
        triggers=triggers,
        active=True,
    )


def run(tenant, location, transcript, values, **extra):
    with pin_tenant(tenant.id):
        return pipeline.run(
            tenant_id=tenant.id,
            location_id=location.id,
            symptoms=extract.extract(transcript),
            settings=values,
            **extra,
        )


# ---------------------------------------------------------------------------
# 1 · extraction
# ---------------------------------------------------------------------------


def test_the_handoffs_transcript_produces_the_handoffs_four_chips():
    """Acceptance 1 · **diarrea, fiebre, adulto, tratamiento activo · losartán**,
    and the duration is a fact rather than a fifth chip."""
    facts = extract.extract(HANDOFF)
    chips = [one for one in facts if one["kind"] != "duration"]
    assert [one["label"] for one in chips] == [
        "diarrea",
        "fiebre",
        "adulto",
        "tratamiento activo · losartán",
    ]
    assert [one["key"] for one in chips] == ["diarrhea", "fever", "adult", "losartan"]
    assert [one["kind"] for one in chips] == [
        "symptom",
        "symptom",
        "population",
        "active_treatment",
    ]
    duration = [one for one in facts if one["kind"] == "duration"]
    assert duration and duration[0]["value"] == 2.0


def test_a_temperature_rides_on_the_fever_chip_and_a_denial_rides_on_its_own():
    """The two facts the filter tells apart: a fever with a number on it, and a
    fever the customer says they do not have."""
    hot = {one["key"]: one for one in extract.extract(HOT)}
    assert hot["fever"]["value"] == 39.0
    assert hot["fever"]["label"] == "fiebre 39 °C"

    cold = {one["key"]: one for one in extract.extract("Diarrea sin fiebre, adulto")}
    assert cold["fever"]["negated"] is True
    assert cold["fever"]["label"] == "sin fiebre"


def test_a_molecule_nobody_said_they_take_is_not_an_active_treatment():
    """**The lead word is required.** A transcript naming the molecule the
    cashier is about to sell is not a statement that the customer takes it, and
    an interaction clause fired by the product on the counter is a filter that
    removes exactly the thing it was asked about."""
    named = extract.extract("Necesito losartán de 50")
    assert not [one for one in named if one["kind"] == "active_treatment"]
    taking = extract.extract("Adulto, toma losartán")
    assert [one["key"] for one in taking if one["kind"] == "active_treatment"] == [
        "losartan"
    ]


def test_a_word_inside_another_word_is_not_a_symptom():
    """`tos` inside `estomago` would put a cough chip on every stomach complaint
    in the country."""
    facts = extract.extract("Dolor de estómago desde ayer")
    assert "cough" not in {one["key"] for one in facts}
    assert "abdominal_pain" in {one["key"] for one in facts}


# ---------------------------------------------------------------------------
# 3 · the safety filter
# ---------------------------------------------------------------------------


def test_a_blocking_warning_removes_the_product_and_never_annotates_it(
    tenant_a, sede_a
):
    """Acceptance 5 and check 1 · **both figures fall by exactly one**, the item
    is in `excluded` naming the row that removed it, and it appears on no card.

    *Wrong* is a loperamida card present carrying the warning as its text — that
    is a filter that ran after ranking (§7).
    """
    digestive, _salts, loperamide = shelf(tenant_a, sede_a)
    values = configure(tenant_a, digestive)
    warning = warn(
        tenant_a, loperamide, text=LOPERAMIDA_TEXT, triggers=LOPERAMIDA_TRIGGERS
    )

    before = run(tenant_a, sede_a, HANDOFF, values)
    conditional = [
        card for card in before.cards if card["type"] == SuggestionType.CONDITIONAL
    ]
    assert len(conditional) == 1
    assert conditional[0]["item_name"] == "Loperamida 2 mg × 12"
    # **The reason is the warning's own text, verbatim.**
    assert conditional[0]["reason"] == LOPERAMIDA_TEXT
    assert conditional[0]["warning_id"] == str(warning.id)

    after = run(tenant_a, sede_a, HOT, values)
    assert after.candidate_count == before.candidate_count - 1
    assert len(after.cards) == len(before.cards) - 1
    assert "Loperamida 2 mg × 12" not in {card["item_name"] for card in after.cards}
    removed = [one for one in after.excluded if one["reason"] == "warning_blocking"]
    assert removed == [
        {
            "item_id": str(loperamide.id),
            "item_name": "Loperamida 2 mg × 12",
            "reason": "warning_blocking",
            "warning_id": str(warning.id),
        }
    ]


def test_removing_the_fever_chip_brings_the_card_back_as_a_condition(tenant_a, sede_a):
    """Acceptance 6 · the trigger is a question nobody put to the customer
    again, so the card returns with the warning's own text as its reason."""
    digestive, _salts, loperamide = shelf(tenant_a, sede_a)
    values = configure(tenant_a, digestive)
    warn(tenant_a, loperamide, text=LOPERAMIDA_TEXT, triggers=LOPERAMIDA_TRIGGERS)

    facts = [one for one in extract.extract(HOT) if one["key"] != "fever"]
    with pin_tenant(tenant_a.id):
        outcome = pipeline.run(
            tenant_id=tenant_a.id,
            location_id=sede_a.id,
            symptoms=facts,
            settings=values,
        )
    conditional = [
        card for card in outcome.cards if card["type"] == SuggestionType.CONDITIONAL
    ]
    assert [card["reason"] for card in conditional] == [LOPERAMIDA_TEXT]


def test_prescription_controlled_and_expired_are_never_on_a_card(tenant_a, sede_a):
    """Acceptance 7 and 8 · §7 names the first; this stage takes the other two.

    A controlled medicine proposed by software to a person who is not a
    pharmacist is the worst available version of this feature, and a lapsed
    registration is a state Botica surfaces rather than recommends.
    """
    digestive, _salts, _loperamide = shelf(tenant_a, sede_a)
    values = configure(tenant_a, digestive)
    for name, flags in (
        ("Con fórmula", {"requires_prescription": True}),
        ("Controlado", {"controlled": True}),
        ("Registro vencido", {"invima_status": "expired"}),
    ):
        item = make_item(tenant_a, name, category=digestive, **flags)
        price(tenant_a, item, "1000")
        stock(
            tenant_a, sede_a, item, make_lot(tenant_a, item, code=f"L-{name[:3]}"), 50
        )

    outcome = run(tenant_a, sede_a, HANDOFF, values)
    offered = {card["item_name"] for card in outcome.cards}
    assert offered.isdisjoint({"Con fórmula", "Controlado", "Registro vencido"})
    reasons = {one["item_name"]: one["reason"] for one in outcome.excluded}
    assert reasons["Con fórmula"] == "requires_prescription"
    assert reasons["Controlado"] == "controlled"
    assert reasons["Registro vencido"] == "invima_expired"


def test_an_item_this_sede_has_none_of_is_not_a_candidate(tenant_a, sede_a):
    """The filter's first step, and the reason card C's empty state is a **stock**
    statement rather than a history one."""
    digestive, salts, loperamide = shelf(tenant_a, sede_a)
    values = configure(tenant_a, digestive)
    with pin_tenant(tenant_a.id):
        from core.models import StockOnHand

        StockOnHand.objects.filter(item=loperamide).update(quantity=0)
    outcome = run(tenant_a, sede_a, HANDOFF, values)
    assert {card["item_name"] for card in outcome.cards} == {salts.name}
    assert [one["reason"] for one in outcome.excluded] == ["out_of_stock"]


# ---------------------------------------------------------------------------
# The three outcomes
# ---------------------------------------------------------------------------


def extraction(transcript):
    return filters.Extraction(extract.extract(transcript))


@pytest.mark.parametrize(
    "transcript,expected",
    [
        # Stated with no temperature, and the blood half never asked: nobody has
        # ruled it out.
        (HANDOFF, filters.UNRESOLVED),
        # 39 clears 38,5.
        (HOT, filters.SATISFIED),
        # The fever is denied and 37 does not clear the threshold either way,
        # but the blood clause is still a question nobody put.
        ("Diarrea sin fiebre, adulto", filters.UNRESOLVED),
    ],
)
def test_the_handoffs_trigger_has_the_three_outcomes_the_document_names(
    transcript, expected
):
    assert filters.evaluate(LOPERAMIDA_TRIGGERS, extraction(transcript)) == expected


def test_a_measured_fever_below_the_threshold_decides_the_clause_false():
    """A temperature that was given and does not clear the bar is **decided**,
    which is what separates *irrelevant* from *unresolved*."""
    single = [{"symptom": "fever", "operator": ">", "value": 38.5, "unit": "celsius"}]
    assert (
        filters.evaluate(single, extraction("Diarrea con fiebre de 37,5"))
        == filters.IRRELEVANT
    )


def test_an_age_decides_another_age_false_and_a_chronic_state_does_not():
    """Saying *adulto* settles that the customer is not a *niño*; it settles
    nothing about *diabético*."""
    child = [{"population": "child"}]
    diabetic = [{"population": "diabetic"}]
    adult = extraction("Adulto con tos")
    assert filters.evaluate(child, adult) == filters.IRRELEVANT
    assert filters.evaluate(diabetic, adult) == filters.UNRESOLVED


def test_naming_one_treatment_decides_the_others_false():
    """*"toma losartán"* is an answer to *"¿está tomando algo?"*, and a counter
    that read it as evidence about warfarina would put an interaction caution on
    every card for every customer who answered."""
    warfarin = [{"interacts_with_ingredient": "warfarina"}]
    assert filters.evaluate(warfarin, extraction(HANDOFF)) == filters.IRRELEVANT
    assert (
        filters.evaluate(warfarin, extraction("Dolor de cabeza, adulto"))
        == filters.UNRESOLVED
    )


def test_only_do_not_suggest_if_turns_an_unresolved_trigger_into_a_card():
    """The asymmetry card C rests on: `do_not_suggest_if` asks *has anyone ruled
    this out?* and the other two ask *does this apply?*"""
    assert filters.makes_conditional(
        ItemWarningType.DO_NOT_SUGGEST_IF, WarningSeverity.BLOCKING, filters.UNRESOLVED
    )
    assert not filters.makes_conditional(
        ItemWarningType.CONTRAINDICATION, WarningSeverity.BLOCKING, filters.UNRESOLVED
    )
    assert filters.makes_conditional(
        ItemWarningType.INTERACTION, WarningSeverity.ADVISORY, filters.SATISFIED
    )
    assert not filters.makes_conditional(
        ItemWarningType.DO_NOT_SUGGEST_IF, WarningSeverity.BLOCKING, filters.IRRELEVANT
    )


# ---------------------------------------------------------------------------
# The closed vocabulary
# ---------------------------------------------------------------------------


def test_a_trigger_outside_the_vocabulary_is_refused_naming_the_key():
    """**This closure is the load-bearing decision of the safety layer.** A
    trigger naming a key the extractor cannot emit never fires, and nothing
    anywhere raises."""
    with pytest.raises(InvalidTrigger) as refused:
        check_triggers([{"symptom": "gastroenteritis"}])
    assert "gastroenteritis" in str(refused.value)

    with pytest.raises(InvalidTrigger):
        check_triggers([{"sintoma": "fever"}])
    with pytest.raises(InvalidTrigger):
        check_triggers([{"symptom": "fever", "operator": "≈", "value": 38.5}])
    with pytest.raises(InvalidTrigger):
        check_triggers([{"population": "teenager"}])

    assert check_triggers(LOPERAMIDA_TRIGGERS) == [
        {"symptom": "fever", "operator": ">", "value": 38.5, "unit": "celsius"},
        {"symptom": "blood_in_stool"},
    ]


# ---------------------------------------------------------------------------
# 4 · rank, and label
# ---------------------------------------------------------------------------


def test_card_c_draws_one_card_per_type_and_leaves_an_empty_slot_empty(
    tenant_a, sede_a
):
    """Acceptance 28 · **nothing occupies the third slot** where no rule covers
    the anchor. This is a new tenant's normal first state and not an error
    state (§1)."""
    digestive, _salts, loperamide = shelf(tenant_a, sede_a)
    values = configure(tenant_a, digestive)
    warn(tenant_a, loperamide, text=LOPERAMIDA_TEXT, triggers=LOPERAMIDA_TRIGGERS)
    for index in range(4):
        item = make_item(tenant_a, f"Suero {index}", category=digestive)
        price(tenant_a, item, "2000")
        stock(tenant_a, sede_a, item, make_lot(tenant_a, item, code=f"L-S{index}"), 30)

    outcome = run(tenant_a, sede_a, HANDOFF, values)
    assert [card["type"] for card in outcome.cards] == [
        SuggestionType.FIRST_CHOICE,
        SuggestionType.CONDITIONAL,
    ]
    assert outcome.candidate_count > len(outcome.cards)


def test_the_first_card_carries_a_reason_written_for_its_own_symptom(tenant_a, sede_a):
    """`symptom_primary` is templated per symptom key, which is what makes the
    line a sentence a cashier repeats out loud rather than a label."""
    digestive, _salts, _loperamide = shelf(tenant_a, sede_a)
    values = configure(tenant_a, digestive)
    outcome = run(tenant_a, sede_a, HANDOFF, values)
    first = outcome.cards[0]
    assert first["reason_code"] == "symptom_primary"
    assert first["reason"] == (
        "repone la pérdida de líquidos, que es lo que más pesa en estos casos"
    )


def test_the_price_on_a_card_is_the_price_in_force_at_this_sede(tenant_a, sede_a):
    """The figure a card prints and the figure S4 stamps on the line are read
    the same way, from the same rows — not close, the same."""
    digestive, salts, _loperamide = shelf(tenant_a, sede_a)
    values = configure(tenant_a, digestive)
    price(tenant_a, salts, "4100", location=sede_a, days_back=1)
    outcome = run(tenant_a, sede_a, HANDOFF, values)
    card = next(one for one in outcome.cards if one["item_name"] == salts.name)
    assert card["price"] == Decimal("4100.00")


def test_an_empty_symptom_map_seeds_nothing_and_says_so(tenant_a, sede_a):
    """The configuration state, which is **not** the cold-start floor: step 2's
    surface-form fallback covers a key the map does not cover, and a fallback
    that fires on every key is not a fallback."""
    _digestive, _salts, _loperamide = shelf(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        values = assistant_settings.write(
            Tenant.objects.get(id=tenant_a.id), {"symptom_category_map": {}}
        )
    outcome = run(tenant_a, sede_a, HANDOFF, values)
    assert outcome.unmapped_keys == ["diarrhea", "fever"]
