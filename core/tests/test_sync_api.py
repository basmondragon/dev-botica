"""The thirteen endpoints, the device credential, and the boundary A4 draws.

The sharpest check here is the parameter sweep: **two failures look alike and
only one of them is a vulnerability.** A predicate scoped to the wrong sede is a
bug the pull tests catch. A parameter that overrides a *correct* predicate is a
till reading another sede's rows on request, and nothing in those tests would
notice -- the predicate is right, and the request simply went around it.
"""

import uuid

import pytest
from django.utils import timezone

from core.models import (
    AuditLog,
    Customer,
    Device,
    ItemPrice,
    PriceSource,
    SyncConflict,
    SyncConflictType,
    Tenant,
)
from core.sync import registry
from core.tenancy import pin_tenant
from core.tests.conftest import make_location
from core.tests.test_sync_pull import make_device, make_item

pytestmark = pytest.mark.django_db


def settle(model, tenant, seconds=30):
    """Push a table's rows behind the safety horizon.

    The pull serves only rows at or below `now - pull_safety_horizon_seconds`,
    so a fixture written a microsecond ago is invisible by design. Backdating is
    what a test does instead of sleeping two seconds -- and a check whose
    baseline is silently empty is a check that would pass against a broken
    predicate too.
    """
    model._default_manager.filter(tenant=tenant).update(
        updated_at=timezone.now() - timezone.timedelta(seconds=seconds)
    )


def headers(key, **extra):
    return {
        "HTTP_X_BOTICA_DEVICE_KEY": key,
        "HTTP_X_BOTICA_APP_VERSION": "0.1.0",
        "HTTP_X_BOTICA_STORAGE_PERSISTED": "true",
        **extra,
    }


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------


def test_a_cashier_claims_their_own_sede_and_the_key_is_returned_once(
    client_as, tenant_a, sede_a, cashier_a
):
    """Criterion 1, and criterion 23's first audit row."""
    client = client_as(cashier_a)
    response = client.post(
        "/api/devices/claim",
        {"label": "Caja 1", "location_id": str(sede_a.id)},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["device_key"].startswith("bkd_")
    assert body["device"]["label"] == "Caja 1"

    with pin_tenant(tenant_a.id):
        device = Device.objects.get(id=body["device"]["id"])
        # The key is never readable back from the server -- what is stored is a
        # digest, and the office list has no column that could leak it.
        assert device.device_key_hash != body["device_key"]
        assert AuditLog.objects.filter(
            entity_type="devices", entity_id=device.id, action="create"
        ).exists()

    # The record panel does not carry the key either.
    read = client.get(f"/api/devices/{body['device']['id']}")
    assert read.status_code == 403  # a cashier does not reach the office list


def test_a_cashier_cannot_claim_another_sede(client_as, tenant_a, sede_a, cashier_a):
    suba = make_location(tenant_a, "SUB", "Suba")
    response = client_as(cashier_a).post(
        "/api/devices/claim",
        {"label": "Caja 1", "location_id": str(suba.id)},
        content_type="application/json",
    )
    assert response.status_code == 403


def test_two_tills_at_one_sede_cannot_share_a_label(
    client_as, tenant_a, sede_a, cashier_a
):
    client = client_as(cashier_a)
    body = {"label": "Caja 1", "location_id": str(sede_a.id)}
    assert (
        client.post(
            "/api/devices/claim", body, content_type="application/json"
        ).status_code
        == 200
    )
    clash = client.post("/api/devices/claim", body, content_type="application/json")
    assert clash.status_code == 409
    assert "Caja 1" in clash.json()["detail"]


def test_required_persistence_refuses_a_browser_that_was_denied(
    client_as, tenant_a, sede_a, owner_a, cashier_a
):
    """Criterion 16 · `storage_persistence_policy = required` makes an evictable
    till a hard stop, and it stops it at installation rather than at a counter."""
    office = client_as(owner_a)
    current = office.get("/api/settings/sync").json()
    written = office.patch(
        "/api/settings/sync",
        {**current, "storage_persistence_policy": "required"},
        content_type="application/json",
    )
    assert written.status_code == 200

    denied = client_as(cashier_a).post(
        "/api/devices/claim",
        {"label": "Caja 1", "location_id": str(sede_a.id)},
        content_type="application/json",
        HTTP_X_BOTICA_STORAGE_PERSISTED="false",
    )
    assert denied.status_code == 409
    assert "almacenamiento protegido" in denied.json()["detail"]


# ---------------------------------------------------------------------------
# The device credential
# ---------------------------------------------------------------------------


def test_a_sync_call_needs_the_session_and_the_key(
    client_as, tenant_a, sede_a, cashier_a
):
    """A4 · neither alone is sufficient."""
    device, key = make_device(tenant_a, sede_a)
    client = client_as(cashier_a)

    assert client.get("/api/sync/registry").status_code == 401
    assert client.get("/api/sync/registry", **headers("bkd_wrong")).status_code == 401
    assert client.get("/api/sync/registry", **headers(key)).status_code == 200

    # And the key alone, with no session, is refused by the permission
    # dependency every endpoint in the product runs behind.
    from django.test import Client

    assert Client().get("/api/sync/registry", **headers(key)).status_code == 401


def test_a_device_key_from_another_network_resolves_to_nothing(
    client_as, tenant_a, tenant_b, sede_a, cashier_a
):
    """The lookup is by hash and runs **inside the pin**, so a key issued in
    another network is answered exactly as a bad key is."""
    other_sede = make_location(tenant_b, "EST", "La Estrella")
    _device, other_key = make_device(tenant_b, other_sede)
    response = client_as(cashier_a).get("/api/sync/registry", **headers(other_key))
    assert response.status_code == 401


def test_a_revoked_device_is_refused_before_any_predicate_is_built(
    client_as, tenant_a, sede_a, cashier_a, owner_a
):
    """Criterion 18 · its next sync fails on the key, and its local data and
    outbox are the office's problem to read, not ours to wipe."""
    device, key = make_device(tenant_a, sede_a)
    revoked = client_as(owner_a).post(f"/api/devices/{device.id}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    refused = client_as(cashier_a).get("/api/sync/registry", **headers(key))
    assert refused.status_code == 401
    assert refused.json()["detail"] == "este equipo fue dado de baja"

    with pin_tenant(tenant_a.id):
        assert SyncConflict.objects.filter(
            type=SyncConflictType.DEVICE_REVOKED, device=device
        ).exists()
        assert AuditLog.objects.filter(
            entity_type="devices", entity_id=device.id, action="revoke"
        ).exists()


# ---------------------------------------------------------------------------
# The location comes from the device, and no parameter moves it
# ---------------------------------------------------------------------------


PARAMETERS = ("location_id", "location", "sede", "filter[location_id]")


def test_no_parameter_anywhere_moves_the_location(
    client_as, tenant_a, sede_a, cashier_a
):
    """Criterion 29 · Suba's id in every place a parameter can reach the
    handler, and every answer is Chapinero's, byte-identical to the call with no
    parameter at all."""
    suba = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a, "Losartán 50 mg")
    today = timezone.localdate()
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        location=sede_a,
        price="3900.00",
        effective_from=today,
        source=PriceSource.MANUAL,
    )
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        location=suba,
        price="5100.00",
        effective_from=today,
        source=PriceSource.MANUAL,
    )
    settle(ItemPrice, tenant_a)
    _device, key = make_device(tenant_a, sede_a)
    client = client_as(cashier_a)

    def prices(**query):
        response = client.get(
            "/api/sync/pull",
            {"collection": "item_prices", **query},
            **headers(key),
        )
        assert response.status_code == 200, response.content
        return sorted(one["price"] for one in response.json()["documents"])

    baseline = prices()
    assert baseline == ["3900.00"], (
        "an empty baseline would make every comparison below pass against a "
        "predicate that serves nothing"
    )
    for name in PARAMETERS:
        assert prices(**{name: str(suba.id)}) == baseline, (
            f"the parameter {name!r} moved the location; a single Suba price on "
            "a Chapinero till is the whole finding"
        )

    # A header, which is the other place a parameter reaches a handler.
    with_header = client.get(
        "/api/sync/pull",
        {"collection": "item_prices"},
        HTTP_X_BOTICA_LOCATION=str(suba.id),
        **headers(key),
    )
    assert sorted(one["price"] for one in with_header.json()["documents"]) == baseline

    # The digest and the registry are built on the same predicate, and the
    # digest is the endpoint most likely to grow a parameter later.
    for path in ("/api/sync/digest", "/api/sync/registry"):
        plain = client.get(path, **headers(key)).json()
        steered = client.get(path, {"location_id": str(suba.id)}, **headers(key)).json()
        plain.pop("server_time"), steered.pop("server_time")
        assert plain == steered


def test_the_location_parameter_is_absent_from_the_schema(client_as, owner_a):
    """A parameter that is not honoured today is still a parameter somebody
    eventually wires to the predicate. It must not be in the endpoint's schema
    at all."""
    import json
    from pathlib import Path

    schema = json.loads(Path("schema/openapi.json").read_text())
    for path in ("/api/sync/pull", "/api/sync/digest", "/api/sync/registry"):
        names = {
            parameter["name"]
            for parameter in schema["paths"][path]["get"].get("parameters", [])
        }
        assert "location_id" not in names
        assert "location" not in names


def test_a_revoked_key_fails_on_the_key_and_not_on_the_predicate(
    client_as, tenant_a, sede_a, cashier_a, owner_a
):
    """The ordering half of criterion 29: a security check that passes only when
    the key is good has tested the happy path."""
    suba = make_location(tenant_a, "SUB", "Suba")
    device, key = make_device(tenant_a, sede_a)
    client_as(owner_a).post(f"/api/devices/{device.id}/revoke")
    response = client_as(cashier_a).get(
        "/api/sync/pull",
        {"collection": "item_prices", "location_id": str(suba.id)},
        **headers(key),
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Push over HTTP
# ---------------------------------------------------------------------------


def push_body(document, name="Ana Gómez", row_id=None, **payload):
    return {
        "batch_id": "batch-1",
        "client_time": timezone.now().isoformat(),
        "rows": [
            {
                "collection": "customers",
                "client_uuid": str(uuid.uuid4()),
                "occurred_at": timezone.now().isoformat(),
                "payload": {
                    "id": str(row_id or uuid.uuid4()),
                    "document_type": "CC",
                    "document": document,
                    "name": name,
                    **payload,
                },
            }
        ],
    }


def test_a_push_applies_and_stamps_both_clocks(client_as, tenant_a, sede_a, cashier_a):
    """§5 rule 4 · `occurred_at` is stored exactly as the device sent it, and
    the skew the server computed is displayed and never applied to it."""
    device, key = make_device(tenant_a, sede_a)
    client = client_as(cashier_a)
    body = push_body("1020304050")
    # A device two days fast. It keeps syncing.
    ahead = (timezone.now() + timezone.timedelta(days=2)).isoformat()
    response = client.post(
        "/api/sync/push",
        body,
        content_type="application/json",
        **headers(key, HTTP_X_BOTICA_DEVICE_CLOCK=ahead),
    )
    assert response.status_code == 200, response.content
    assert response.json()["results"][0]["outcome"] == "applied"

    with pin_tenant(tenant_a.id):
        device.refresh_from_db()
        assert device.clock_skew_ms > 0
        assert device.last_pushed_at is not None
        assert Customer.objects.filter(document="1020304050").count() == 1


def test_a_replayed_push_returns_duplicate_and_creates_no_second_row(
    client_as, tenant_a, sede_a, cashier_a
):
    """Criterion 10 · the case that actually happens is the push that timed out
    **after** the server committed."""
    _device, key = make_device(tenant_a, sede_a)
    client = client_as(cashier_a)
    body = push_body("1020304050")

    first = client.post(
        "/api/sync/push", body, content_type="application/json", **headers(key)
    ).json()
    again = client.post(
        "/api/sync/push", body, content_type="application/json", **headers(key)
    ).json()

    assert first["results"][0]["outcome"] == "applied"
    assert again["results"][0]["outcome"] == "duplicate"
    assert again["results"][0]["id"] == first["results"][0]["id"]
    with pin_tenant(tenant_a.id):
        assert Customer.objects.filter(document="1020304050").count() == 1


def test_a_foreign_tenant_push_is_refused_whole_over_http(
    client_as, tenant_a, tenant_b, sede_a, cashier_a
):
    """Criterion 11 · and the conflict it raises names the device and the
    collection and carries no document number."""
    _device, key = make_device(tenant_a, sede_a)
    body = push_body("1111111111")
    body["rows"].append(
        {
            "collection": "customers",
            "client_uuid": str(uuid.uuid4()),
            "occurred_at": None,
            "payload": {
                "id": str(uuid.uuid4()),
                "document_type": "CC",
                "document": "2222222222",
                "name": "Otra red",
                "tenant_id": str(tenant_b.id),
            },
        }
    )
    response = client_as(cashier_a).post(
        "/api/sync/push", body, content_type="application/json", **headers(key)
    )
    # 200 with `batch_outcome: rejected`, not a 4xx: S0's middleware rolls the
    # request's transaction back on any status at or above 400, and it would
    # take the conflict row with it. The office learning that a till tried to
    # write into another network is the entire point of the refusal.
    assert response.status_code == 200
    body_out = response.json()
    assert body_out["batch_outcome"] == "rejected"
    assert {one["outcome"] for one in body_out["results"]} == {"rejected"}
    with pin_tenant(tenant_a.id):
        assert (
            Customer.objects.filter(document__in=["1111111111", "2222222222"]).count()
            == 0
        )
        conflict = SyncConflict.objects.get(type=SyncConflictType.FOREIGN_TENANT)
        assert "2222222222" not in str(conflict.detail)
        assert conflict.detail["batch_id"] == "batch-1"


# ---------------------------------------------------------------------------
# The office
# ---------------------------------------------------------------------------


def test_the_office_list_is_paginated_and_carries_its_open_conflicts(
    client_as, tenant_a, sede_a, owner_a
):
    for index in range(3):
        make_device(tenant_a, sede_a, label=f"Caja {index + 1}")
    response = client_as(owner_a).get("/api/devices", {"page_size": 25})
    assert response.status_code == 200
    body = response.json()
    assert body["row_count"] == 3
    assert {row["open_conflicts"] for row in body["rows"]} == {0}


def test_moving_a_device_to_another_sede_changes_what_it_pulls(
    client_as, tenant_a, sede_a, cashier_a, owner_a
):
    """Criterion 13 · without a re-claim. The pull answers the new
    `location_id`, which is how the till knows to reset its location-scoped
    collections."""
    suba = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a, "Losartán 50 mg")
    today = timezone.localdate()
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        location=sede_a,
        price="3900.00",
        effective_from=today,
        source=PriceSource.MANUAL,
    )
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        location=suba,
        price="5100.00",
        effective_from=today,
        source=PriceSource.MANUAL,
    )
    settle(ItemPrice, tenant_a)
    device, key = make_device(tenant_a, sede_a)
    till = client_as(cashier_a)

    before = till.get(
        "/api/sync/pull", {"collection": "item_prices"}, **headers(key)
    ).json()
    assert before["location_id"] == str(sede_a.id)
    assert [one["price"] for one in before["documents"]] == ["3900.00"]

    moved = client_as(owner_a).patch(
        f"/api/devices/{device.id}",
        {"location_id": str(suba.id)},
        content_type="application/json",
    )
    assert moved.status_code == 200

    after = till.get(
        "/api/sync/pull", {"collection": "item_prices"}, **headers(key)
    ).json()
    assert after["location_id"] == str(suba.id)
    assert [one["price"] for one in after["documents"]] == ["5100.00"]

    with pin_tenant(tenant_a.id):
        assert AuditLog.objects.filter(
            entity_type="devices", entity_id=device.id, action="update"
        ).exists()


def test_the_sync_settings_group_leaves_S0s_group_untouched(
    client_as, tenant_a, owner_a
):
    """Criterion 22, rule 5 · a read-modify-write of the whole column takes out
    S0's group and is only noticed on the screen it belongs to, weeks later."""
    client = client_as(owner_a)
    client.patch(
        "/api/settings/tenant",
        {
            "name": "Droguerías La 45",
            "nit": "901.245.778-3",
            "legal_name": "Droguerías La 45 S.A.S.",
            "timezone": "America/Bogota",
        },
        content_type="application/json",
    )
    current = client.get("/api/settings/sync").json()
    assert current["pull_interval_seconds"] == 8

    written = client.patch(
        "/api/settings/sync",
        {**current, "pull_interval_seconds": 12},
        content_type="application/json",
    )
    assert written.status_code == 200
    assert client.get("/api/settings/sync").json()["pull_interval_seconds"] == 12

    tenant_group = client.get("/api/settings/tenant").json()
    assert tenant_group["legal_name"] == "Droguerías La 45 S.A.S."
    with pin_tenant(tenant_a.id):
        stored = Tenant.objects.get(id=tenant_a.id).settings
    assert set(stored) == {"tenant", "sync"}


def test_a_setting_outside_its_bounds_is_refused(client_as, owner_a):
    """A pull interval of zero is a till hammering the server; a horizon of zero
    reopens the commit-ordering hole the horizon exists to close."""
    client = client_as(owner_a)
    current = client.get("/api/settings/sync").json()
    response = client.patch(
        "/api/settings/sync",
        {**current, "pull_safety_horizon_seconds": 0},
        content_type="application/json",
    )
    assert response.status_code == 422


def test_a_cashier_does_not_reach_the_office_surfaces(client_as, cashier_a):
    """A4 · an office browser is never a device, and a till is never the office."""
    client = client_as(cashier_a)
    assert client.get("/api/devices").status_code == 403
    assert client.get("/api/sync/conflicts").status_code == 403
    assert client.get("/api/settings/sync").status_code == 403


def test_the_conflict_queue_closes_a_row_and_never_deletes_it(
    client_as, tenant_a, sede_a, owner_a
):
    device, _key = make_device(tenant_a, sede_a)
    from core.sync import conflicts as conflict_service

    with pin_tenant(tenant_a.id):
        row = conflict_service.raise_conflict(
            device=device,
            type=SyncConflictType.PAYLOAD_REJECTED,
            collection="customers",
            detail={"reason": "name_required", "field": "name"},
        )

    client = client_as(owner_a)
    listed = client.get("/api/sync/conflicts", {"status": "open"}).json()
    assert listed["row_count"] == 1

    closed = client.patch(
        f"/api/sync/conflicts/{row.id}",
        {"status": "dismissed", "note": "El cajero lo volvió a registrar."},
        content_type="application/json",
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "dismissed"
    with pin_tenant(tenant_a.id):
        assert SyncConflict.objects.filter(id=row.id).exists()


def test_every_registry_collection_has_a_cursor_index_in_one_of_two_shapes():
    """The gate rule 9 exists to install here rather than discover at a pilot: a
    collection servable but undeclared, or declared without an index in one of
    the two shapes, fails the build."""
    for collection in registry.COLLECTIONS:
        wanted = (
            ["tenant", "location", "updated_at", "id"]
            if collection.scope == registry.LOCATION_SCOPED
            else ["tenant", "updated_at", "id"]
        )
        # `stock_on_hand` backs two collections and therefore carries both
        # shapes: its own sede's rows range over the location cursor, and the
        # other-location set ranges over the tenant-wide one with the item set
        # as a residual. One index per collection, and both are cursor shapes
        # rather than one compromise that serves neither.
        shapes = [list(index.fields) for index in collection.model._meta.indexes]
        assert wanted in shapes, (
            f"{collection.name} is in the registry with no delta-cursor index "
            f"in the {collection.scope} shape {wanted}"
        )


def test_the_registry_is_the_only_door(client_as, tenant_a, sede_a, cashier_a):
    """A table absent from the registry does not reach a device."""
    _device, key = make_device(tenant_a, sede_a)
    response = client_as(cashier_a).get(
        "/api/sync/pull", {"collection": "audit_log"}, **headers(key)
    )
    assert response.status_code == 422
    assert "items" in response.json()["detail"]
