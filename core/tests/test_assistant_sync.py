"""S2's registry amendment, the two push writers, and the isolation this stage
must not break.

**Four collections**: `cross_sell_rules` and `item_warnings` down to the till
under their predicates, `assistant_queries` and `assistant_suggestions` up
through the outbox. Rules and warnings never reaching a till is the failure that
matters, because an offline assistant then filters on nothing while looking
exactly as if it does (A8, rule 9).
"""

import uuid

import pytest

from core.assistant import settings as assistant_settings
from core.models import (
    AssistantQuery,
    AssistantSuggestion,
    Category,
    CrossSellBasis,
    CrossSellConfidence,
    CrossSellRule,
    ItemWarning,
    ItemWarningSource,
    ItemWarningType,
    SaleLine,
    SuggestionType,
    Tenant,
    WarningSeverity,
)
from core.sync import registry
from core.sync import settings as sync_settings
from core.tenancy import pin_tenant, repin
from core.tests.conftest import make_location
from core.tests.test_counter_push import Till, apply, envelope, price, stock
from core.tests.test_inventory_ledger import make_lot
from core.tests.test_sync_pull import make_device, make_item

pytestmark = pytest.mark.django_db


def pull_all(collection, *, tenant, location, limit=50):
    """Page through one collection the way a device does.

    The options mapping is `sync_settings.options` and not S2's bare defaults,
    because `cross_sell_rules`' membership rule reads two keys the `assistant`
    group owns — which is exactly what that helper exists to carry.
    """
    from datetime import timedelta

    from django.utils import timezone

    from core.sync import pull

    # A row is stamped when its statement runs and becomes visible when its
    # transaction commits, so the pull serves only up to `now()` minus the
    # safety horizon. A test writing rows in the same instant reads them from a
    # clock a minute later rather than sleeping for two seconds.
    later = timezone.now() + timedelta(minutes=1)
    with pin_tenant(tenant.id):
        values = sync_settings.options(Tenant.objects.get(id=tenant.id))
    cursor = pull.ZERO
    seen = []
    for _ in range(50):
        documents, checkpoint, has_more = pull.page(
            collection,
            tenant_id=tenant.id,
            location_id=location.id,
            cursor=cursor,
            limit=limit,
            options=values,
            now=later,
        )
        seen.extend(documents)
        if checkpoint is None or not has_more:
            break
        cursor = pull.parse_cursor(checkpoint["updated_at"], checkpoint["id"])
    return seen


def rule(tenant, location, anchor, partner, **extra):
    fields = dict(
        tenant=tenant,
        location=location,
        item_a=anchor,
        item_b=partner,
        support=40,
        confidence="0.4100",
        lift="2.1000",
        rank=1,
        window="90d",
        algorithm_version="cross-sell-1",
        basis=CrossSellBasis.COUNTER,
        ticket_count=2200,
        confidence_band=CrossSellConfidence.MEDIUM,
    )
    fields.update(extra)
    return CrossSellRule.objects.create(**fields)


def warning(tenant, item, **extra):
    fields = dict(
        tenant=tenant,
        item=item,
        type=ItemWarningType.DO_NOT_SUGGEST_IF,
        text="no ofrecer si hay sangre",
        severity=WarningSeverity.BLOCKING,
        source=ItemWarningSource.CATALOG,
        triggers=[{"symptom": "blood_in_stool"}],
        active=True,
    )
    fields.update(extra)
    return ItemWarning.objects.create(**fields)


def test_the_registry_declares_four_collections_two_of_them_push_only():
    """The gate: rules and warnings pulled with their predicates, offers and
    acceptances pushed and never pulled."""
    pulled = {one.name for one in registry.COLLECTIONS}
    assert {"cross_sell_rules", "item_warnings"} <= pulled
    pushed = {one.name for one in registry.PUSH_ONLY}
    assert {"assistant_queries", "assistant_suggestions"} <= pushed
    assert registry.get("cross_sell_rules").scope == registry.LOCATION_SCOPED
    assert registry.get("item_warnings").scope == registry.TENANT_WIDE
    for name in ("assistant_queries", "assistant_suggestions"):
        assert registry.get(name).push is True
        with pytest.raises(LookupError):
            registry.pullable(name)


def test_a_till_pulls_this_sedes_rules_and_the_networks_and_no_other_sedes(
    tenant_a, sede_a
):
    other = make_location(tenant_a, "SUB", "Suba")
    anchor = make_item(tenant_a, "Suero")
    partner = make_item(tenant_a, "Antidiarreico")
    mine = rule(tenant_a, sede_a, anchor, partner)
    network = rule(tenant_a, None, partner, anchor)
    theirs = rule(tenant_a, other, anchor, partner)

    documents = pull_all(registry.CROSS_SELL_RULES, tenant=tenant_a, location=sede_a)
    served = {one["id"] for one in documents}
    assert str(mine.id) in served
    assert str(network.id) in served
    assert str(theirs.id) not in served
    document = next(one for one in documents if one["id"] == str(mine.id))
    assert document["kind"] == "rule"
    assert document["item_id"] == str(anchor.id)
    assert document["item_b_id"] == str(partner.id)
    assert document["confidence_band"] == CrossSellConfidence.MEDIUM
    # `basis` and `ticket_count` are Ajustes' and Ajustes is online-only, so
    # neither crosses the wire.
    assert "basis" not in document
    assert "ticket_count" not in document


def test_a_rule_below_the_support_floor_never_reaches_a_till(tenant_a, sede_a):
    """The registry's own floor. **The cap itself is the job's**, and this is
    the predicate that keeps a till from carrying what the settings say it
    should not."""
    anchor = make_item(tenant_a, "Suero")
    partner = make_item(tenant_a, "Antidiarreico")
    rule(tenant_a, sede_a, anchor, partner, support=2)
    rule(tenant_a, sede_a, partner, anchor, rank=9)
    documents = pull_all(registry.CROSS_SELL_RULES, tenant=tenant_a, location=sede_a)
    assert [one for one in documents if not one.get("_deleted")] == []


def test_deactivating_a_warning_serves_a_departure_rather_than_deleting_a_row(
    tenant_a, sede_a
):
    """S2 criterion 14 · **no endpoint or migration in this stage hard-deletes a
    row from a registry collection**, or the row lives on every till forever."""
    item = make_item(tenant_a, "Loperamida 2 mg × 12")
    row = warning(tenant_a, item)
    first = pull_all(registry.ITEM_WARNINGS, tenant=tenant_a, location=sede_a)
    assert [one["id"] for one in first] == [str(row.id)]
    assert first[0]["kind"] == "warning"
    assert first[0]["triggers"] == [{"symptom": "blood_in_stool"}]

    with pin_tenant(tenant_a.id):
        row.active = False
        row.save(update_fields=["active", "updated_at"])
    again = pull_all(registry.ITEM_WARNINGS, tenant=tenant_a, location=sede_a)
    departure = next(one for one in again if one["id"] == str(row.id))
    assert departure.get("_deleted") is True


def test_the_policy_store_tells_its_three_streams_apart_by_a_stamped_kind(
    tenant_a, sede_a
):
    """S8's two reference collections share S3's store on the device, so each
    stream's rows are told apart by a `kind` the server stamps — **never a guess
    over which fields are present**, which would delete the wrong rows on a
    reset."""
    from core.models import StockPolicy

    item = make_item(tenant_a, "Suero")
    StockPolicy.objects.create(
        tenant=tenant_a, item=item, location=sede_a, reorder_point=10, source="manual"
    )
    warning(tenant_a, item)
    rule(tenant_a, sede_a, item, make_item(tenant_a, "Antidiarreico"))
    kinds = set()
    for collection in (
        registry.POLICIES,
        registry.ITEM_WARNINGS,
        registry.CROSS_SELL_RULES,
    ):
        for document in pull_all(collection, tenant=tenant_a, location=sede_a):
            kinds.add(document["kind"])
    assert kinds == {"policy", "warning", "rule"}


# ---------------------------------------------------------------------------
# The push
# ---------------------------------------------------------------------------


def counter(tenant, location, user):
    device, _key = make_device(tenant, location)
    category = Category.objects.create(tenant=tenant, name="Digestivo")
    item = make_item(tenant, "Sales de rehidratación oral", category=category)
    lot = make_lot(tenant, item, code="L-SRO")
    price(tenant, item, "3900")
    stock(tenant, location, item, lot, 40)
    with pin_tenant(tenant.id):
        assistant_settings.write(
            Tenant.objects.get(id=tenant.id),
            {"symptom_category_map": {"diarrhea": [str(category.id)]}},
        )
    return device, item, lot


def offline_sale(device, user, item, lot, query_uuid, suggestion_uuid):
    """One offline ticket with a suggestion taken on it, in the order a till
    queues the events: the offer first, then S4's lines and its sale, then the
    attach and the acceptance behind them."""
    till = Till(device, user)
    return [
        envelope(
            "assistant_queries",
            {
                "client_uuid": query_uuid,
                "event": "offer",
                "transcript": "Diarrea, adulto",
                "symptoms": [
                    {
                        "key": "diarrhea",
                        "label": "diarrea",
                        "kind": "symptom",
                        "source": "lexicon",
                    }
                ],
                "candidates": [
                    {
                        "client_uuid": suggestion_uuid,
                        "item_id": str(item.id),
                        "type": SuggestionType.FIRST_CHOICE,
                        "reason": "repone la pérdida de líquidos",
                        "reason_code": "symptom_primary",
                        "price": "3900.00",
                        "rank": 1,
                        "available_quantity": 40,
                    }
                ],
                "candidate_count": 9,
                "recommendation": "Ofrezca Sales de rehidratación oral primero.",
                "bundle_version": "1.test",
                "user_id": str(user.id),
                "user_name": user.name,
            },
        ),
        till.open_shift(),
        till.open_sale(),
        till.line(item, 1, "3900", lot=lot),
        till.payment("cash", "3900"),
        till.close_sale(),
        envelope(
            "assistant_queries",
            {
                "client_uuid": query_uuid,
                "event": "attach",
                "sale_client_uuid": till.sale_key,
            },
        ),
        envelope(
            "assistant_suggestions",
            {
                "client_uuid": suggestion_uuid,
                "event": "accept",
                "sale_line_id": None,
            },
        ),
    ]


def test_an_offline_sale_with_a_suggestion_lands_whole_and_replays_clean(
    tenant_a, sede_a, cashier_a
):
    """Acceptance 16 · three rows on the server, the line flagged, one query —
    and **re-pushing the same batch produces no duplicates of any of them**
    (A5)."""
    device, item, lot = counter(tenant_a, sede_a, cashier_a)
    query_uuid = str(uuid.uuid4())
    suggestion_uuid = str(uuid.uuid4())
    rows = offline_sale(device, cashier_a, item, lot, query_uuid, suggestion_uuid)

    # The till knows the line's own id, because S4's writer keeps the one the
    # device minted.
    line_id = str(uuid.uuid4())
    rows[3]["payload"]["id"] = line_id
    rows[-1]["payload"]["sale_line_id"] = line_id

    first = apply(device, rows, batch_id="offline-1")
    assert [one.outcome for one in first.outcomes].count("rejected") == 0

    with pin_tenant(tenant_a.id):
        query = AssistantQuery.objects.get()
        suggestion = AssistantSuggestion.objects.get()
        line = SaleLine.objects.get()
        assert query.sale_id is not None
        assert query.mode == "local"
        assert query.candidate_count == 9
        # **The cashier who asked, on the offline row too** -- which is the one
        # case per-cashier acceptance would otherwise be blind to.
        assert query.user_id == cashier_a.id
        assert query.user_name == cashier_a.name
        assert suggestion.accepted is True
        assert suggestion.sale_line_id == line.id
        assert line.from_suggestion is True

    again = apply(device, rows, batch_id="offline-1")
    assert [one.outcome for one in again.outcomes].count("rejected") == 0
    with pin_tenant(tenant_a.id):
        assert AssistantQuery.objects.count() == 1
        assert AssistantSuggestion.objects.count() == 1
        assert SaleLine.objects.filter(from_suggestion=True).count() == 1


def test_an_acceptance_for_a_card_the_server_never_saw_is_refused(
    tenant_a, sede_a, cashier_a
):
    """**A suggestion row is never created by the acceptance.** Inventing one
    would create an accepted offer with no offer behind it and inflate exactly
    the numerator this stage exists to keep honest."""
    device, _item, _lot = counter(tenant_a, sede_a, cashier_a)
    result = apply(
        device,
        [
            envelope(
                "assistant_suggestions",
                {
                    "client_uuid": str(uuid.uuid4()),
                    "event": "accept",
                    "sale_line_id": str(uuid.uuid4()),
                },
            )
        ],
        batch_id="orphan",
    )
    assert [one.outcome for one in result.outcomes] == ["rejected"]
    with pin_tenant(tenant_a.id):
        assert AssistantSuggestion.objects.count() == 0


@pytest.mark.django_db(transaction=False)
def test_a_second_tenant_sees_none_of_the_first_ones_rows(
    tenant_a, tenant_b, sede_a, as_runtime_role
):
    """A1 · **reading it only from the tenant that holds nothing proves
    nothing**, so the count runs both ways — and both run as the runtime role,
    because the suite's own role holds BYPASSRLS and would pass for the wrong
    reason."""
    item = make_item(tenant_a, "Suero")
    partner = make_item(tenant_a, "Antidiarreico")
    rule(tenant_a, sede_a, item, partner)
    warning(tenant_a, item)
    stranger = make_location(tenant_b, "EST", "La Estrella")
    theirs = make_item(tenant_b, "Suero")
    rule(tenant_b, stranger, theirs, make_item(tenant_b, "Antidiarreico"))
    warning(tenant_b, theirs)

    as_runtime_role()
    repin(tenant_b.id)
    assert CrossSellRule.objects.filter(tenant_id=tenant_a.id).count() == 0
    assert ItemWarning.objects.filter(tenant_id=tenant_a.id).count() == 0
    assert CrossSellRule.objects.count() == 1

    repin(tenant_a.id)
    assert CrossSellRule.objects.filter(tenant_id=tenant_b.id).count() == 0
    assert ItemWarning.objects.filter(tenant_id=tenant_b.id).count() == 0
    assert CrossSellRule.objects.count() == 1
