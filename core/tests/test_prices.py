"""The price editor, and the negative A11 rests on.

The load tool and this one endpoint are the only two writers of `item_prices` in
the product, and `price_source` admits two values. A price row appearing from
anywhere else is a change to A11, not a bug to patch.
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.catalog import prices
from core.models import AuditLog, Item, ItemPrice, ItemType, PriceSource
from core.tests.test_catalog import make_item


def _post(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


@pytest.fixture
def item(tenant_a):
    return make_item(tenant_a)


@pytest.mark.django_db
def test_a_price_change_closes_the_open_row_in_the_same_transaction(
    client_as, owner_a, item
):
    """Acceptance 9 and 12 · a new row, the previous one closed, and the name of
    the person who typed it."""
    first = _post(
        client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10000"}
    )
    assert first.status_code == 200, first.content
    second = _post(
        client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "11000"}
    )
    assert second.status_code == 200, second.content

    rows = client_as(owner_a).get(f"/api/items/{item.id}/prices").json()
    assert len(rows) == 2
    current = [row for row in rows if row["current"]]
    assert len(current) == 1
    assert Decimal(current[0]["price"]) == Decimal("11000")
    assert current[0]["source"] == "manual"
    assert current[0]["set_by_user_id"] == str(owner_a.id)
    assert current[0]["set_by_name"] == owner_a.name
    closed = [row for row in rows if not row["current"]][0]
    assert closed["effective_to"] is not None
    assert Decimal(closed["price"]) == Decimal("10000")


@pytest.mark.django_db
def test_the_endpoint_never_takes_source_or_author_from_the_body(
    client_as, owner_a, item
):
    """A field a client can set is a field a client can lie about, and the
    second question of every price dispute is who typed it."""
    _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": "9000", "source": "imported", "set_by_user_id": None},
    )
    row = ItemPrice.objects.get(item=item)
    assert row.source == PriceSource.MANUAL
    assert row.set_by_user_id == owner_a.id


@pytest.mark.django_db
def test_price_source_admits_two_values_and_there_is_no_third(tenant_a, item):
    """Acceptance 12 · the enum is the guarantee, not the serialiser."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'price_source' ORDER BY e.enumsortorder"
        )
        assert [row[0] for row in cursor.fetchall()] == ["manual", "imported"]


@pytest.mark.django_db
def test_a_proposal_id_is_refused_while_price_proposals_does_not_exist(
    client_as, owner_a, item
):
    """Acceptance 13 · the column ships nullable from S1 and the producer does
    not, and storing an id that names nothing is worse than refusing it."""
    import uuid

    response = _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": "9000", "proposal_id": str(uuid.uuid4())},
    )
    assert response.status_code == 422
    assert "Precios" in response.json()["detail"]
    assert ItemPrice.objects.filter(proposal_id__isnull=False).count() == 0


@pytest.mark.django_db
def test_a_price_above_a_known_cap_is_refused_naming_it(client_as, owner_a, item):
    """Acceptance 14 · and a null cap is *unknown*, never *uncapped*."""
    item.regulated_max_price = Decimal("15600")
    item.save(update_fields=["regulated_max_price"])

    refused = _post(
        client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "15700"}
    )
    assert refused.status_code == 422
    assert "$15.600" in refused.json()["detail"]

    item.regulated_max_price = None
    item.save(update_fields=["regulated_max_price"])
    accepted = _post(
        client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "15700"}
    )
    assert accepted.status_code == 200


@pytest.mark.django_db
def test_a_future_dated_row_is_not_in_force_until_its_date_and_no_job_runs(
    client_as, owner_a, item
):
    """Acceptance 11 · resolution happens at read time."""
    today = timezone.localdate()
    _post(client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10000"})
    _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": "12000", "effective_from": (today + timedelta(days=7)).isoformat()},
    )
    assert prices.in_force(item.id).price == Decimal("10000")
    assert prices.in_force(item.id, on=today + timedelta(days=7)).price == Decimal(
        "12000"
    )


@pytest.mark.django_db
def test_a_sede_price_applies_at_that_sede_only(client_as, owner_a, item, sede_a):
    """Acceptance 10."""
    _post(client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10000"})
    scoped = _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": "9000", "location_id": str(sede_a.id)},
    )
    assert scoped.status_code == 200, scoped.content
    assert prices.in_force(item.id, location_id=sede_a.id).price == Decimal("9000")
    assert prices.in_force(item.id).price == Decimal("10000")

    # Removing it returns that sede to the network price with no further edit.
    withdrawn = client_as(owner_a).delete(f"/api/item-prices/{scoped.json()['id']}")
    assert withdrawn.status_code == 200
    assert withdrawn.json()["outcome"] == "closed"
    assert prices.in_force(item.id, location_id=sede_a.id).price == Decimal("10000")


@pytest.mark.django_db
def test_a_row_never_in_force_is_removed_and_one_in_force_is_only_closed(
    client_as, owner_a, item
):
    today = timezone.localdate()
    _post(client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10000"})
    future = _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": "12000", "effective_from": (today + timedelta(days=7)).isoformat()},
    ).json()
    response = client_as(owner_a).delete(f"/api/item-prices/{future['id']}")
    assert response.json()["outcome"] == "deleted"
    assert not ItemPrice.objects.filter(id=future["id"]).exists()

    # The network-wide row that *is* in force cannot be withdrawn: an item that
    # should not be sold is deactivated, not left priceless.
    open_row = ItemPrice.objects.get(item=item, effective_to__isnull=True)
    refused = client_as(owner_a).delete(f"/api/item-prices/{open_row.id}")
    assert refused.status_code == 422


@pytest.mark.django_db
def test_backdating_behind_everything_the_scope_holds_is_refused(
    client_as, owner_a, item
):
    today = timezone.localdate()
    _post(client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10000"})
    refused = _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": "9000", "effective_from": (today - timedelta(days=5)).isoformat()},
    )
    assert refused.status_code == 422
    assert "Ya hay un precio con fecha" in refused.json()["detail"]


@pytest.mark.django_db
def test_a_price_dated_ahead_does_not_make_the_item_unrepricable_today(
    client_as, owner_a, item
):
    """The row a new price supersedes is the one whose window contains its own
    start, not simply the open one -- so a repricing dated a week ahead does not
    lock today's price until that week has passed."""
    today = timezone.localdate()
    next_week = today + timedelta(days=7)
    _post(client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10000"})
    _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": "12000", "effective_from": next_week.isoformat()},
    )

    corrected = _post(
        client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10500"}
    )
    assert corrected.status_code == 200, corrected.content
    assert prices.in_force(item.id).price == Decimal("10500")
    # And the row dated ahead is untouched and still arrives on its own date.
    assert prices.in_force(item.id, on=next_week).price == Decimal("12000")
    assert ItemPrice.objects.filter(item=item, effective_to__isnull=True).count() == 1


@pytest.mark.django_db
def test_every_price_write_lands_on_the_audit_trail(client_as, owner_a, item):
    _post(client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10000"})
    _post(client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "11000"})
    rows = AuditLog.objects.filter(entity_type="item_prices").order_by("created_at")
    assert rows.count() == 2
    assert rows.last().before["price"] == "10000.00"
    assert rows.last().after["price"] == "11000.00"
    assert rows.last().actor_user_id == owner_a.id


@pytest.mark.django_db
def test_a_cashier_cannot_read_or_write_a_price_history(client_as, cashier_a, item):
    client = client_as(cashier_a)
    assert client.get(f"/api/items/{item.id}/prices").status_code == 403
    assert (
        _post(client, f"/api/items/{item.id}/prices", {"price": "1"}).status_code == 403
    )


@pytest.mark.django_db
def test_only_one_open_row_per_item_and_scope_at_the_database(tenant_a, item):
    from django.db import IntegrityError, transaction

    today = timezone.localdate()
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        price=Decimal("1"),
        effective_from=today,
        source=PriceSource.IMPORTED,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ItemPrice.objects.create(
            tenant=tenant_a,
            item=item,
            price=Decimal("2"),
            effective_from=today,
            source=PriceSource.IMPORTED,
        )


@pytest.mark.django_db
def test_the_grid_reads_the_network_price_in_one_query(client_as, owner_a, tenant_a):
    for index in range(3):
        one = Item.objects.create(
            tenant=tenant_a,
            type=ItemType.PRODUCT,
            name=f"Producto {index}",
            unit="caja",
            vat_class="excluded",
            invima_status="not_applicable",
        )
        ItemPrice.objects.create(
            tenant=tenant_a,
            item=one,
            price=Decimal(1000 * (index + 1)),
            effective_from=timezone.localdate(),
            source=PriceSource.IMPORTED,
        )
    rows = client_as(owner_a).get("/api/items").json()["rows"]
    assert [Decimal(row["price"]) for row in rows] == [
        Decimal("1000.00"),
        Decimal("2000.00"),
        Decimal("3000.00"),
    ]


@pytest.mark.django_db
def test_creating_an_item_writes_no_price(client_as, owner_a):
    """A11 · "one interactive writer" has to mean one endpoint. A create that
    also priced would be the second, however carefully it routed."""
    import json as _json

    response = client_as(owner_a).post(
        "/api/items",
        data=_json.dumps(
            {
                "type": "product",
                "name": "Sin precio",
                "unit": "caja",
                "vat_class": "excluded",
                "price": "9000",
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["prices"] == []
    assert ItemPrice.objects.count() == 0


@pytest.mark.django_db
def test_a_price_names_its_author_even_after_that_person_is_deleted(
    client_as, owner_a, admin_a, item
):
    """S0's referential rule: a stage referencing `users` stamps the identity it
    needs at write time. A hand-typed price whose author was hard-deleted must
    not read as one nobody typed."""
    _post(client_as(admin_a), f"/api/items/{item.id}/prices", {"price": "9000"})
    row = ItemPrice.objects.get(item=item)
    assert row.set_by_name == admin_a.name

    client_as(owner_a).delete(f"/api/users/{admin_a.id}")
    row.refresh_from_db()
    assert row.set_by_user_id is None
    assert row.set_by_name == admin_a.name
    assert row.source == PriceSource.MANUAL

    shown = client_as(owner_a).get(f"/api/items/{item.id}/prices").json()[0]
    assert shown["set_by_name"] == admin_a.name


@pytest.mark.django_db
def test_withdrawing_a_future_price_names_the_row_on_the_audit_trail(
    client_as, owner_a, item
):
    """`Model.delete()` sets the instance pk to None, so an audit row that read
    `row.id` afterwards named no entity at all."""
    today = timezone.localdate()
    _post(client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10000"})
    future = _post(
        client_as(owner_a),
        f"/api/items/{item.id}/prices",
        {"price": "12000", "effective_from": (today + timedelta(days=7)).isoformat()},
    ).json()
    client_as(owner_a).delete(f"/api/item-prices/{future['id']}")

    row = AuditLog.objects.filter(entity_type="item_prices", action="delete").first()
    assert str(row.entity_id) == future["id"]
    assert row.before["item"] == str(item.id)


@pytest.mark.django_db
def test_a_cashier_reads_the_price_in_force_and_not_the_history(
    client_as, cashier_a, owner_a, item
):
    """The gate on `GET /api/items/{id}/prices` would be one join away from
    meaningless if the item detail carried every closed row and its author."""
    _post(client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "10000"})
    _post(client_as(owner_a), f"/api/items/{item.id}/prices", {"price": "11000"})

    seen = client_as(cashier_a).get(f"/api/items/{item.id}").json()["prices"]
    assert len(seen) == 1
    assert seen[0]["current"] is True
    assert seen[0]["set_by_name"] is None

    # An owner still gets the whole history on the same endpoint.
    full = client_as(owner_a).get(f"/api/items/{item.id}").json()["prices"]
    assert len(full) == 2
