"""The three jobs, and the one call that deliberately is not among them.

**The model call is not a job.** It runs inline on `POST
/api/assistant/queries` under `model_timeout_ms`, because a queued call would
need the till to poll for prose it has already rendered a local version of.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core import gateway
from core.assistant import jobs, prose
from core.assistant import settings as assistant_settings
from core.models import (
    AssistantMode,
    AssistantQuery,
    ItemWarning,
    ItemWarningSource,
    ItemWarningType,
    Tenant,
    WarningSeverity,
)
from core.tenancy import pin_tenant
from core.tests.test_sync_pull import make_item

pytestmark = pytest.mark.django_db


def query(tenant, location, *, days_back=0, **extra):
    fields = dict(
        tenant=tenant,
        location=location,
        client_uuid=timezone.now().isoformat() and __import__("uuid").uuid4(),
        transcript="Diarrea, adulto",
        symptoms=[
            {
                "key": "diarrhea",
                "label": "diarrea",
                "kind": "symptom",
                "source": "lexicon",
            }
        ],
        recommendation="Ofrezca suero primero.",
        recommendation_secondary="La sede tiene tres referencias disponibles.",
        mode=AssistantMode.LOCAL,
        candidate_count=9,
    )
    fields.update(extra)
    row = AssistantQuery.objects.create(**fields)
    if days_back:
        stamp = timezone.now() - timedelta(days=days_back)
        AssistantQuery.objects.filter(id=row.id).update(
            recorded_at=stamp, occurred_at=stamp
        )
        row.refresh_from_db()
    return row


def test_the_purge_takes_the_words_and_leaves_the_shape(tenant_a, sede_a):
    """Acceptance 23 · what survives is `location_id`, `sale_id`, `mode`,
    `model`, `cost_usd`, `latency_ms`, the timestamps and the suggestions with
    `accepted` — **exactly what every metric needs and none of what Ley 1581 is
    about**."""
    with pin_tenant(tenant_a.id):
        assistant_settings.write(
            Tenant.objects.get(id=tenant_a.id),
            {"retain_transcripts": True, "transcript_retention_days": 30},
        )
        old = query(tenant_a, sede_a, days_back=40, cost_usd=Decimal("0.0031"))
        fresh = query(tenant_a, sede_a, days_back=1)

    report = jobs.assistant_transcript_purge(
        tenant_id=str(tenant_a.id), purge_date=timezone.localdate()
    )
    assert report["purged"] == 1
    with pin_tenant(tenant_a.id):
        old.refresh_from_db()
        fresh.refresh_from_db()
        assert old.transcript == ""
        assert old.symptoms == []
        assert old.recommendation == ""
        # The shape survives, which is what the metrics read.
        assert old.location_id == sede_a.id
        assert old.mode == AssistantMode.LOCAL
        assert old.cost_usd == Decimal("0.003100")
        assert fresh.transcript == "Diarrea, adulto"


def test_with_retention_off_the_purge_takes_everything_it_finds(tenant_a, sede_a):
    """**This job exists and runs whichever way §11.3 is answered**, because the
    transcript exists the moment a cashier types it. A till at that setting
    never pushes one; this is what catches the rows written before somebody
    turned it off."""
    with pin_tenant(tenant_a.id):
        assistant_settings.write(
            Tenant.objects.get(id=tenant_a.id), {"retain_transcripts": False}
        )
        row = query(tenant_a, sede_a)
    jobs.assistant_transcript_purge(
        tenant_id=str(tenant_a.id), purge_date=timezone.localdate()
    )
    with pin_tenant(tenant_a.id):
        row.refresh_from_db()
        assert row.transcript == ""
        assert row.symptoms == []


def test_the_health_check_finds_a_warning_nothing_has_ever_fired(tenant_a, sede_a):
    """**This is the check that keeps the closed vocabulary honest.** A warning
    naming a key the extractor cannot emit never fires and nothing anywhere
    raises, so the screen says *"esta advertencia nunca se ha activado"* and
    somebody looks at it."""
    with pin_tenant(tenant_a.id):
        item = make_item(tenant_a, "Loperamida 2 mg × 12")
        fires = ItemWarning.objects.create(
            tenant=tenant_a,
            item=item,
            type=ItemWarningType.DO_NOT_SUGGEST_IF,
            text="no ofrecer si hay fiebre",
            severity=WarningSeverity.BLOCKING,
            source=ItemWarningSource.CATALOG,
            triggers=[{"symptom": "diarrhea"}],
        )
        dormant = ItemWarning.objects.create(
            tenant=tenant_a,
            item=item,
            type=ItemWarningType.CONTRAINDICATION,
            text="no ofrecer si hay ardor al orinar",
            severity=WarningSeverity.ADVISORY,
            source=ItemWarningSource.CATALOG,
            triggers=[{"symptom": "burning_urination"}],
        )
        query(tenant_a, sede_a)
        report = jobs.health_report(Tenant.objects.get(id=tenant_a.id))

    named = {one["id"] for one in report["dormant_warnings"]}
    assert str(dormant.id) in named
    assert str(fires.id) not in named
    assert report["queries_considered"] == 1
    assert report["queries_without_chips"] == 0
    assert "fever" in report["unmapped_symptom_keys"]


def test_the_rejection_rate_counts_only_the_queries_a_model_answered(tenant_a, sede_a):
    """A rate whose denominator counted offline queries would fall whenever the
    fibre did."""
    with pin_tenant(tenant_a.id):
        for _ in range(3):
            query(tenant_a, sede_a, mode=AssistantMode.MODEL)
        query(tenant_a, sede_a, mode=AssistantMode.LOCAL, output_check_passed=False)
        query(tenant_a, sede_a, mode=AssistantMode.LOCAL)
        report = jobs.health_report(Tenant.objects.get(id=tenant_a.id))
    assert report["rejection_rate"] == pytest.approx(0.25)
    assert report["rejection_alert"] is True


# ---------------------------------------------------------------------------
# The spend cap, enforced on the request path
# ---------------------------------------------------------------------------


def test_the_cap_is_a_read_over_this_months_own_rows(tenant_a, sede_a, settings):
    """**A cap a job enforces is a cap that is a day late.** It is one indexed
    aggregate, run before the call, and it is off the sale's critical path by
    construction because nothing about a sale waits on that endpoint (§4)."""
    settings.BOTICA_GATEWAY_BASE_URL = "https://example.invalid/v1"
    settings.BOTICA_GATEWAY_API_KEY = "not-a-real-key"
    with pin_tenant(tenant_a.id):
        tenant = Tenant.objects.get(id=tenant_a.id)
        assistant_settings.write(
            tenant, {"model_enabled": True, "monthly_spend_cap_usd": 1.0}
        )
        tenant.refresh_from_db()
        assert gateway.enabled_for(tenant) is True

        query(tenant_a, sede_a, cost_usd=Decimal("0.4"))
        query(tenant_a, sede_a, cost_usd=Decimal("0.3"))
        assert gateway.spend_this_month(tenant_a.id) == Decimal("0.700000")
        assert gateway.enabled_for(tenant) is True

        query(tenant_a, sede_a, cost_usd=Decimal("0.5"))
        assert gateway.enabled_for(tenant) is False
        # Last month's spend is last month's.
        AssistantQuery.objects.update(recorded_at=timezone.now() - timedelta(days=60))
        assert gateway.spend_this_month(tenant_a.id) == Decimal("0")


def test_the_switch_is_read_before_the_call_and_is_off_until_somebody_answers(
    tenant_a, settings
):
    """§11.3 · **a *no* costs no code**, and the default is the *no*."""
    settings.BOTICA_GATEWAY_BASE_URL = "https://example.invalid/v1"
    settings.BOTICA_GATEWAY_API_KEY = "not-a-real-key"
    with pin_tenant(tenant_a.id):
        tenant = Tenant.objects.get(id=tenant_a.id)
        assert gateway.enabled_for(tenant) is False
        assistant_settings.write(tenant, {"model_enabled": True})
        tenant.refresh_from_db()
        assert gateway.enabled_for(tenant) is True
        # **`enabled` is not consulted**: that switch removes the column from
        # Mostrador and says nothing about whether a vendor may be called.
        assistant_settings.write(tenant, {"enabled": False})
        tenant.refresh_from_db()
        assert gateway.enabled_for(tenant) is True


# ---------------------------------------------------------------------------
# The output check
# ---------------------------------------------------------------------------


CARDS = [
    {
        "item_id": "11111111-1111-4111-8111-111111111111",
        "item_name": "Sales de rehidratación oral",
        "type": "first_choice",
        "reason": "repone la pérdida de líquidos",
        "reason_code": "symptom_primary",
        "price": Decimal("3900"),
        "available_quantity": 14,
        "warning_id": None,
    },
    {
        "item_id": "22222222-2222-4222-8222-222222222222",
        "item_name": "Butilbromuro de hioscina 20 mg × 12",
        "type": "conditional",
        "reason": "no ofrecer si la fiebre pasa de 38,5 °C o si hay sangre",
        "reason_code": "warning_conditional",
        "price": Decimal("8400"),
        "available_quantity": 8,
        "warning_id": "33333333-3333-4333-8333-333333333333",
    },
]

SYMPTOMS = [
    {"key": "diarrhea", "label": "diarrea", "kind": "symptom", "source": "lexicon"},
    {
        "key": "losartan",
        "label": "tratamiento activo · losartán",
        "kind": "active_treatment",
        "source": "lexicon",
    },
]


#: The catalog this pharmacy stocks, as the endpoint passes it: what makes
#: *"names a product name outside the candidate list"* decidable.
MOLECULES = {"Loperamida", "Acetaminofén", "Butilbromuro de hioscina"}


def check(payload):
    return prose.check(
        payload, cards=CARDS, symptoms=SYMPTOMS, known_molecules=MOLECULES
    )


def test_a_clean_answer_passes_and_carries_only_what_is_read():
    passed, flags, cleaned = check(
        {
            "recommendation": "Priorice la rehidratación.",
            "recommendation_secondary": "La sede tiene las dos referencias disponibles.",
            "reasons": {CARDS[0]["item_id"]: "repone líquidos y sales"},
            "extra": "ignored",
        }
    )
    assert passed and flags == []
    assert cleaned["reasons"] == {CARDS[0]["item_id"]: "repone líquidos y sales"}
    assert "extra" not in cleaned


@pytest.mark.parametrize(
    "payload,flag",
    [
        (None, prose.FLAG_UNPARSEABLE),
        ({"recommendation": 4, "recommendation_secondary": ""}, prose.FLAG_SHAPE),
        (
            {
                "recommendation": "Probablemente sea una gastroenteritis.",
                "recommendation_secondary": "",
                "reasons": {},
            },
            prose.FLAG_CONDITION_NAMED,
        ),
        (
            {
                "recommendation": "Usted tiene un cuadro fuerte.",
                "recommendation_secondary": "",
                "reasons": {},
            },
            prose.FLAG_DIAGNOSTIC_PHRASING,
        ),
        (
            {
                "recommendation": "Suspenda el losartán mientras dure el cuadro.",
                "recommendation_secondary": "",
                "reasons": {},
            },
            prose.FLAG_TREATMENT_CHANGE,
        ),
        (
            {
                "recommendation": "Ofrezca loperamida primero.",
                "recommendation_secondary": "",
                "reasons": {},
            },
            prose.FLAG_UNKNOWN_ITEM,
        ),
        (
            {
                "recommendation": "Priorice la rehidratación.",
                "recommendation_secondary": "",
                "reasons": {"44444444-4444-4444-8444-444444444444": "otra cosa"},
            },
            prose.FLAG_UNKNOWN_ITEM,
        ),
        (
            {
                "recommendation": "Priorice la rehidratación.",
                "recommendation_secondary": "",
                "reasons": {CARDS[1]["item_id"]: "úselo con confianza"},
            },
            prose.FLAG_REWROTE_WARNING,
        ),
        (
            {
                "recommendation": "x" * 400,
                "recommendation_secondary": "",
                "reasons": {},
            },
            prose.FLAG_LENGTH,
        ),
    ],
)
def test_the_output_check_discards_and_names_the_rule_that_fired(payload, flag):
    """Acceptance 11 and 12 · **on rejection nothing model-written renders**, and
    the query row names which rule fired. The cashier sees no error, no toast
    and no empty card."""
    passed, flags, cleaned = check(payload)
    assert passed is False
    assert flag in flags
    assert cleaned == {}


def test_the_model_may_add_a_chip_and_may_never_remove_one():
    """**The one thing the model must not be able to do.** A model that could
    narrow the symptom set could un-filter a product the safety layer had
    excluded."""
    merged = prose.merge_model_symptoms(
        SYMPTOMS,
        [
            {"key": "fever", "label": "fiebre", "kind": "symptom"},
            {"key": "gastroenteritis", "label": "x", "kind": "symptom"},
        ],
    )
    keys = [one["key"] for one in merged]
    assert keys == ["diarrhea", "losartan", "fever"]
    assert merged[-1]["source"] == "model"
    # Nothing the lexicon found is gone.
    assert prose.merge_model_symptoms(SYMPTOMS, []) == SYMPTOMS


def test_what_the_model_is_sent_carries_no_transcript_where_retention_is_off():
    """**Not sent:** the customer's identity, `customers` in any form, the
    ticket's other lines' prices, the tenant's name, or anything from
    `item_warnings` beyond the `conditional` cards' own text."""
    prompt = prose.build_prompt(
        symptoms=SYMPTOMS, cards=CARDS, transcript="", location_name="Chapinero"
    )
    assert "transcripcion" not in prompt
    assert "Butilbromuro" in prompt
    with_words = prose.build_prompt(
        symptoms=SYMPTOMS,
        cards=CARDS,
        transcript="Lleva dos días con diarrea",
        location_name="Chapinero",
    )
    assert "transcripcion" in with_words
