"""The endpoints, the acceptance that is written twice by one act, and the two
switches.

**One implementation of acceptance, reachable two ways.** The accept endpoint
and S2's push writer call the same service, so an acceptance recorded online and
one recorded from a queue that sat through a blackout are the same act and
cannot drift — which is what checks 10 and 13 rest on.
"""

import uuid
from decimal import Decimal

import pytest

from core.assistant import settings as assistant_settings
from core.models import (
    AssistantMode,
    AssistantQuery,
    AssistantSuggestion,
    AuditLog,
    Category,
    ItemWarning,
    SaleLine,
    SuggestionType,
    Tenant,
)
from core.tenancy import pin_tenant
from core.tests.conftest import make_user
from core.tests.test_counter_push import Till, apply, price, stock
from core.tests.test_inventory_ledger import make_lot
from core.tests.test_sync_pull import make_device, make_item
from core.models import Role, Sale

pytestmark = pytest.mark.django_db

HANDOFF = "Lleva dos días con diarrea y algo de fiebre. Adulto, toma losartán."


def counter(tenant, location, user):
    """A device, one shelf reference and a ticket that closed with it on."""
    device, key = make_device(tenant, location)
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
    return device, key, item, lot


def ask_body(item, **extra):
    return {
        "client_uuid": str(uuid.uuid4()),
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
                "client_uuid": str(uuid.uuid4()),
                "item_id": str(item.id),
                "type": "first_choice",
                "reason": "repone la pérdida de líquidos",
                "reason_code": "symptom_primary",
                "price": "3900.00",
                "rank": 1,
                "available_quantity": 40,
            }
        ],
        "candidate_count": 12,
        "transcript": HANDOFF,
        "bundle_version": "1.test",
        **extra,
    }


def post(client, key, body):
    return client.post(
        "/api/assistant/queries",
        data=body,
        content_type="application/json",
        headers={"X-Botica-Device-Key": key},
    )


def test_asking_writes_the_offer_rows_and_is_idempotent_on_the_client_uuid(
    tenant_a, sede_a, cashier_a, client_as
):
    """Acceptance 15 · a retried ask after a timeout returns the same row.

    **With no model configured the query runs in `local` mode with no gateway
    call at all**, which is the ordinary configuration and not a failure.
    """
    device, key, item, _lot = counter(tenant_a, sede_a, cashier_a)
    del device
    client = client_as(cashier_a)
    body = ask_body(item)

    first = post(client, key, body)
    assert first.status_code == 200, first.content
    answer = first.json()
    assert answer["mode"] == AssistantMode.LOCAL
    assert answer["recommendation"]
    assert len(answer["suggestions"]) == 1

    again = post(client, key, body)
    assert again.json()["id"] == answer["id"]
    with pin_tenant(tenant_a.id):
        assert AssistantQuery.objects.count() == 1
        assert AssistantSuggestion.objects.count() == 1
        query = AssistantQuery.objects.get()
        assert query.candidate_count == 12
        assert query.bundle_version == "1.test"
        assert query.user_id == cashier_a.id
        # **Stamped, not joined**: §2 hard-deletes a `users` row and the log has
        # to keep saying who asked.
        assert query.user_name == cashier_a.name
        assert query.location_id == sede_a.id


def test_the_server_never_trusts_a_client_about_what_may_be_suggested(
    tenant_a, sede_a, cashier_a, client_as
):
    """Check 2 · a till that is out of date about the catalog would otherwise be
    able to offer what §7 says is never offered, and the check would pass on
    every screen anyone looked at while being false in the table."""
    _device, key, item, _lot = counter(tenant_a, sede_a, cashier_a)
    forbidden = make_item(tenant_a, "Con fórmula", requires_prescription=True)
    body = ask_body(item)
    body["candidates"].append(
        {
            **body["candidates"][0],
            "client_uuid": str(uuid.uuid4()),
            "item_id": str(forbidden.id),
            "rank": 2,
        }
    )
    client = client_as(cashier_a)
    assert post(client, key, body).status_code == 200
    with pin_tenant(tenant_a.id):
        assert AssistantSuggestion.objects.count() == 1
        assert AssistantSuggestion.objects.get().item_id == item.id
        assert AssistantQuery.objects.get().excluded == [
            {
                "item_id": str(forbidden.id),
                "item_name": "Con fórmula",
                "reason": "requires_prescription",
                "warning_id": None,
            }
        ]


def test_accepting_flags_the_line_and_credits_it_once(
    tenant_a, sede_a, cashier_a, client_as
):
    """Acceptance 14 and check 10 · **one line is credited to at most one
    suggestion**, which is the whole of what keeps the numerator honest."""
    device, key, item, lot = counter(tenant_a, sede_a, cashier_a)
    till = Till(device, cashier_a)
    apply(
        device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 1, "3900", lot=lot),
            till.payment("cash", "3900"),
            till.close_sale(),
        ],
    )
    client = client_as(cashier_a)
    answer = post(client, key, ask_body(item)).json()
    suggestion_id = answer["suggestions"][0]["id"]
    with pin_tenant(tenant_a.id):
        line = SaleLine.objects.get()

    accepted = client.post(
        f"/api/assistant/suggestions/{suggestion_id}/accept",
        data={"sale_line_id": str(line.id)},
        content_type="application/json",
    )
    assert accepted.status_code == 200
    assert accepted.json()["accepted"] is True

    # Idempotent: the same line again changes nothing.
    again = client.post(
        f"/api/assistant/suggestions/{suggestion_id}/accept",
        data={"sale_line_id": str(line.id)},
        content_type="application/json",
    )
    assert again.status_code == 200

    with pin_tenant(tenant_a.id):
        line.refresh_from_db()
        assert line.from_suggestion is True
        row = AssistantSuggestion.objects.get()
        assert row.accepted and row.accepted_at is not None
        assert row.sale_line_id == line.id
        assert SaleLine.objects.filter(from_suggestion=True).count() == 1
        assert AssistantSuggestion.objects.filter(sale_line__isnull=False).count() == 1


def test_a_second_line_cannot_take_a_suggestion_another_line_already_has(
    tenant_a, sede_a, cashier_a, client_as
):
    """`UNIQUE (tenant_id, sale_line_id)` is what keeps the numerator honest, and
    a silent overwrite would move one line's credit from one card to another."""
    device, key, item, lot = counter(tenant_a, sede_a, cashier_a)
    till = Till(device, cashier_a)
    apply(
        device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 1, "3900", lot=lot, position=0),
            till.line(item, 2, "3900", lot=lot, position=1),
            till.payment("cash", "11700"),
            till.close_sale(),
        ],
    )
    client = client_as(cashier_a)
    answer = post(client, key, ask_body(item)).json()
    suggestion_id = answer["suggestions"][0]["id"]
    with pin_tenant(tenant_a.id):
        first, second = SaleLine.objects.order_by("position")
    client.post(
        f"/api/assistant/suggestions/{suggestion_id}/accept",
        data={"sale_line_id": str(first.id)},
        content_type="application/json",
    )
    refused = client.post(
        f"/api/assistant/suggestions/{suggestion_id}/accept",
        data={"sale_line_id": str(second.id)},
        content_type="application/json",
    )
    assert refused.status_code == 409


def test_the_kill_switch_stops_every_request_leaving_the_instance(
    tenant_a, sede_a, cashier_a, client_as, settings, monkeypatch
):
    """Acceptance 21 · with `model_enabled` false, **no request leaves the
    instance for any query** and every card is local."""
    _device, key, item, _lot = counter(tenant_a, sede_a, cashier_a)
    settings.BOTICA_GATEWAY_BASE_URL = "https://example.invalid/v1"
    settings.BOTICA_GATEWAY_API_KEY = "not-a-real-key"

    calls = {"n": 0}

    def _explode(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("the gateway was called with the switch off")

    monkeypatch.setattr("core.gateway.complete", _explode)
    client = client_as(cashier_a)
    for _ in range(3):
        answer = post(client, key, ask_body(item)).json()
        assert answer["mode"] == AssistantMode.LOCAL
    assert calls["n"] == 0
    with pin_tenant(tenant_a.id):
        assert AssistantQuery.objects.filter(mode=AssistantMode.MODEL).count() == 0
        assert set(AssistantQuery.objects.values_list("cost_usd", flat=True)) == {
            Decimal("0")
        }


def test_the_column_switch_refuses_the_endpoint_outright(
    tenant_a, sede_a, cashier_a, client_as
):
    """Acceptance 22 · with `enabled` false **no `assistant_queries` row is
    written by any action**."""
    _device, key, item, _lot = counter(tenant_a, sede_a, cashier_a)
    with pin_tenant(tenant_a.id):
        assistant_settings.write(Tenant.objects.get(id=tenant_a.id), {"enabled": False})
    client = client_as(cashier_a)
    assert post(client, key, ask_body(item)).status_code == 403
    with pin_tenant(tenant_a.id):
        assert AssistantQuery.objects.count() == 0


def test_superseding_takes_the_un_accepted_offers_out_of_the_denominator(
    tenant_a, sede_a, cashier_a, client_as
):
    _device, key, item, _lot = counter(tenant_a, sede_a, cashier_a)
    client = client_as(cashier_a)
    answer = post(client, key, ask_body(item)).json()
    stamped = client.post(f"/api/assistant/queries/{answer['id']}/supersede")
    assert stamped.status_code == 200
    with pin_tenant(tenant_a.id):
        assert AssistantQuery.objects.get().superseded_at is not None


# ---------------------------------------------------------------------------
# The safety layer's own endpoints
# ---------------------------------------------------------------------------


def test_a_warning_write_outside_the_vocabulary_is_a_field_scope_refusal(
    tenant_a, sede_a, owner_a, client_as
):
    """A `triggers` value outside the vocabulary is a refusal naming the key
    (§B.10.3), **never a save that half-works**."""
    item = make_item(tenant_a, "Loperamida 2 mg × 12")
    client = client_as(owner_a)
    refused = client.post(
        "/api/item-warnings",
        data={
            "item_id": str(item.id),
            "type": "do_not_suggest_if",
            "text": "no ofrecer si hay sangre",
            "severity": "blocking",
            "triggers": [{"symptom": "gastroenteritis"}],
        },
        content_type="application/json",
    )
    assert refused.status_code == 422
    assert "gastroenteritis" in refused.json()["detail"]
    with pin_tenant(tenant_a.id):
        assert ItemWarning.objects.count() == 0


def test_editing_and_deactivating_a_warning_land_audit_rows(
    tenant_a, sede_a, owner_a, client_as
):
    """Acceptance 25 · actor, entity and before/after on every elevated-role
    mutation, and **`DELETE` deactivates**: a registry collection that is
    hard-deleted lives on every till forever (S2, criterion 14)."""
    item = make_item(tenant_a, "Loperamida 2 mg × 12")
    client = client_as(owner_a)
    created = client.post(
        "/api/item-warnings",
        data={
            "item_id": str(item.id),
            "type": "do_not_suggest_if",
            "text": "no ofrecer si hay sangre",
            "severity": "blocking",
            "triggers": [{"symptom": "blood_in_stool"}],
        },
        content_type="application/json",
    ).json()

    client.patch(
        f"/api/item-warnings/{created['id']}",
        data={"text": "no ofrecer si hay sangre en la deposición"},
        content_type="application/json",
    )
    deactivated = client.delete(f"/api/item-warnings/{created['id']}")
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False

    with pin_tenant(tenant_a.id):
        assert ItemWarning.objects.filter(id=created["id"]).exists()
        rows = list(
            AuditLog.objects.filter(entity_type="item_warning").order_by("created_at")
        )
        assert [row.action for row in rows] == ["create", "update", "archive"]
        assert all(row.actor_user_id == owner_a.id for row in rows)
        assert rows[1].before["text"] != rows[1].after["text"]


def test_a_cashier_is_refused_the_office_surfaces_by_name(
    tenant_a, sede_a, cashier_a, client_as
):
    """Acceptance 24 · a denial naming the role required — not a redirect and
    not a blank pane (§B.8.3)."""
    client = client_as(cashier_a)
    for path in (
        "/api/settings/assistant",
        "/api/item-warnings",
        "/api/cross-sell-rules",
        "/api/assistant/metrics",
        "/api/assistant/queries",
    ):
        answer = client.get(path)
        assert answer.status_code == 403, path
        assert "Propietaria" in answer.json()["detail"]


def test_the_settings_group_is_written_through_s0s_helper(tenant_a, owner_a, client_as):
    """Rule 5 · one `jsonb_set`, every other group untouched, key for key."""
    with pin_tenant(tenant_a.id):
        tenant = Tenant.objects.get(id=tenant_a.id)
        tenant.settings = {
            "pricing": {"rounding_unit": 50},
            "sync": {"pull_page_size": 500},
        }
        tenant.save(update_fields=["settings"])

    client = client_as(owner_a)
    answer = client.patch(
        "/api/settings/assistant",
        data={"suggestion_card_count": 4, "cross_sell_min_support": 30},
        content_type="application/json",
    )
    assert answer.status_code == 200
    assert answer.json()["suggestion_card_count"] == 4
    with pin_tenant(tenant_a.id):
        held = Tenant.objects.get(id=tenant_a.id).settings
        assert held["pricing"] == {"rounding_unit": 50}
        assert held["sync"] == {"pull_page_size": 500}
        assert held["assistant"]["cross_sell_min_support"] == 30
        assert AuditLog.objects.filter(entity_type="settings.assistant").count() == 1


def test_the_settings_group_carries_fourteen_keys_and_none_about_the_notice(
    tenant_a, owner_a, client_as
):
    """Check 3 · **there is nowhere to put the flag** (A8)."""
    answer = client_as(owner_a).get("/api/settings/assistant").json()
    assert len(answer) == 14
    assert not [key for key in answer if "notice" in key or "aviso" in key]
    assert set(answer) == set(assistant_settings.DEFAULTS)


def test_a_symptom_map_naming_another_networks_category_is_refused(
    tenant_a, tenant_b, owner_a, client_as
):
    """A map naming a category this tenant does not own would seed nothing, and
    would do it silently."""
    stranger = Category.objects.create(tenant=tenant_b, name="Digestivo")
    refused = client_as(owner_a).patch(
        "/api/settings/assistant",
        data={"symptom_category_map": {"diarrhea": [str(stranger.id)]}},
        content_type="application/json",
    )
    assert refused.status_code == 422


def test_the_bundle_carries_the_lexicon_and_the_two_settings_the_column_needs(
    tenant_a, sede_a, cashier_a, client_as
):
    """It is cached with the device record and refreshed when its version
    changes — the same treatment S2 gives the sede's own name and code."""
    _device, key, _item, _lot = counter(tenant_a, sede_a, cashier_a)
    answer = client_as(cashier_a).get(
        "/api/assistant/bundle", headers={"X-Botica-Device-Key": key}
    )
    assert answer.status_code == 200
    bundle = answer.json()
    assert bundle["symptoms"]["diarrhea"]["label"] == "diarrea"
    assert "diarrea" in bundle["symptoms"]["diarrhea"]["forms"]
    assert bundle["enabled"] is True
    assert bundle["suggestion_card_count"] == 3
    assert bundle["strings"]["local"]["primary_first"]
    assert bundle["version"].startswith("1.")
    # **The notice is deliberately not in it**: a notice delivered over the wire
    # is a notice a deployment can empty (A8).
    assert "Botica no diagnostica" not in answer.content.decode()


def test_the_query_log_renders_only_what_retention_has_kept(
    tenant_a, sede_a, cashier_a, owner_a, client_as
):
    """§B.9.2 tier 3 · a purged row renders `—` with its own reading, **never a
    blank and never a zero**."""
    _device, key, item, _lot = counter(tenant_a, sede_a, cashier_a)
    post(client_as(cashier_a), key, ask_body(item))
    with pin_tenant(tenant_a.id):
        AssistantQuery.objects.update(
            transcript="", recommendation="", recommendation_secondary="", symptoms=[]
        )
    rows = client_as(owner_a).get("/api/assistant/queries").json()["rows"]
    assert rows[0]["purged"] is True
    assert rows[0]["symptoms"] is None


def test_the_metrics_endpoint_reports_the_three_definitions(
    tenant_a, sede_a, cashier_a, owner_a, client_as
):
    """*Hands off* · S9 reads these and derives none of them a second way."""
    device, key, item, lot = counter(tenant_a, sede_a, cashier_a)
    till = Till(device, cashier_a)
    apply(
        device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 1, "3900", lot=lot),
            till.payment("cash", "3900"),
            till.close_sale(),
        ],
    )
    with pin_tenant(tenant_a.id):
        sale = Sale.objects.get()
        line = SaleLine.objects.get()
    client = client_as(cashier_a)
    answer = post(
        client, key, ask_body(item, sale_client_uuid=str(sale.client_uuid))
    ).json()
    client.post(
        f"/api/assistant/suggestions/{answer['suggestions'][0]['id']}/accept",
        data={"sale_line_id": str(line.id)},
        content_type="application/json",
    )
    figures = client_as(owner_a).get("/api/assistant/metrics").json()
    assert figures["offered"] == 1
    assert figures["accepted"] == 1
    assert figures["rate"] == 1.0
    assert figures["suggested_tickets"] == 1
    assert figures["plain_tickets"] == 0
    assert figures["local_queries"] == 1


def test_a_platform_admin_outside_a_tenant_reaches_nothing(tenant_a, sede_a, client_as):
    """S0's pin is the boundary, and this stage adds no way around it."""
    outsider = make_user(tenant_a, Role.ADMIN, "other@la45.co")
    del outsider
    with pin_tenant(tenant_a.id):
        assert AssistantSuggestion.objects.count() == 0
        assert AssistantQuery.objects.count() == 0
        assert SuggestionType.FIRST_CHOICE == "first_choice"
