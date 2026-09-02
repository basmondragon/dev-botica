"""The delta cursor: the tuple, the horizon, departures and the digest.

Each test names the property that stops holding rather than the assertion that
moved -- a cursor that skips a tie, a departure that never arrives, a location
predicate that a parameter went around.
"""

import uuid
from datetime import timedelta

import pytest
from django.db.models import Q
from django.utils import timezone

from core.models import Device, DeviceStatus, Item, ItemPrice, PriceSource
from core.sync import devices as device_service, digest, pull, registry
from core.sync import settings as sync_settings
from core.tests.conftest import make_location

pytestmark = pytest.mark.django_db


def make_device(tenant, location, label="Caja 1"):
    key, key_hash = device_service.mint()
    device = Device.objects.create(
        tenant=tenant,
        location=location,
        label=label,
        # Allocated the way the claim endpoint allocates it, so a fixture cannot
        # produce a device the product could not have created.
        code=device_service.allocate_code(tenant.id, location),
        device_key_hash=key_hash,
        status=DeviceStatus.ACTIVE,
    )
    return device, key


def make_item(tenant, name, **overrides):
    fields = dict(
        tenant=tenant,
        type="product",
        name=name,
        unit="unidad",
        vat_class="excluded",
        invima_status="not_applicable",
        active=True,
    )
    fields.update(overrides)
    return Item.objects.create(**fields)


def options():
    return dict(sync_settings.DEFAULTS)


def pull_all(collection, *, tenant_id, location_id, limit=2, now=None):
    """Page through a collection the way a device does, and answer every
    document it received."""
    cursor = pull.ZERO
    seen = []
    for _ in range(50):
        documents, checkpoint, has_more = pull.page(
            collection,
            tenant_id=tenant_id,
            location_id=location_id,
            cursor=cursor,
            limit=limit,
            options=options(),
            now=now,
        )
        seen.extend(documents)
        if checkpoint is None:
            break
        cursor = pull.parse_cursor(checkpoint["updated_at"], checkpoint["id"])
        if not has_more:
            break
    return seen


def test_two_rows_written_in_one_instant_both_arrive(tenant_a, sede_a):
    """Criterion 7 · the tuple is load-bearing.

    One `UPDATE` touching two rows stamps both `updated_at` to the same
    microsecond. A page that ends between them must have somewhere the next page
    can start, or one row is lost for good and nothing raises.
    """
    for index in range(4):
        make_item(tenant_a, f"Producto {index}")
    stamp = timezone.now() - timedelta(minutes=1)
    Item.objects.filter(tenant=tenant_a).update(updated_at=stamp)
    assert Item.objects.filter(tenant=tenant_a, updated_at=stamp).count() == 4, (
        "the fixture did not actually produce a tie"
    )

    seen = pull_all(
        registry.ITEMS, tenant_id=tenant_a.id, location_id=sede_a.id, limit=2
    )
    assert len({one["id"] for one in seen}) == 4


def test_a_cursor_on_updated_at_alone_loses_a_row(tenant_a, sede_a):
    """The demonstration criterion 7 asks for: **delete the tuple's `id`
    component and watch a row never arrive.**

    It pages the same collection twice — once with the tuple cursor the product
    uses, once with the `updated_at > $C` cursor it would have if `_after`'s
    second branch were removed — over a set of rows that all share one
    microsecond. The tuple sees every row; the timestamp alone loses the rest of
    the tie set the moment a page ends inside it. Mutating the product and
    re-running is the other half of the same check, and this is the half that
    ships.
    """
    for index in range(6):
        make_item(tenant_a, f"Producto {index}")
    stamp = timezone.now() - timedelta(minutes=1)
    Item.objects.filter(tenant=tenant_a).update(updated_at=stamp)
    assert Item.objects.filter(tenant=tenant_a, updated_at=stamp).count() == 6, (
        "the fixture did not actually produce a tie"
    )

    ceiling = pull.horizon(options())

    def page_with(after):
        """Page to exhaustion under one cursor rule, and answer what arrived."""
        seen, cursor, guard = [], pull.ZERO, 0
        while guard < 20:
            guard += 1
            rows = list(
                Item.objects.filter(tenant=tenant_a, updated_at__lte=ceiling)
                .filter(after(cursor))
                .order_by("updated_at", "id")
                .values("id", "updated_at")[:2]
            )
            if not rows:
                break
            seen.extend(rows)
            cursor = pull.Cursor(rows[-1]["updated_at"], str(rows[-1]["id"]))
        return {str(row["id"]) for row in seen}

    whole_tuple = page_with(pull._after)
    timestamp_only = page_with(
        lambda cursor: Q() if cursor.is_start else Q(updated_at__gt=cursor.updated_at)
    )

    assert len(whole_tuple) == 6
    assert len(timestamp_only) == 2, (
        "the timestamp-only cursor is expected to serve one page and then "
        "advance past the whole tie set"
    )
    assert whole_tuple - timestamp_only, (
        "removing `id` from the cursor lost nothing, which means the fixture "
        "did not put a page boundary inside the tie"
    )


def test_the_horizon_holds_back_rows_too_recent_to_order(tenant_a, sede_a):
    """A row stamped inside the horizon is not served, because a transaction
    that stamped earlier may still be uncommitted and would be lost behind it."""
    make_item(tenant_a, "Recién escrito")
    seen = pull_all(
        registry.ITEMS, tenant_id=tenant_a.id, location_id=sede_a.id, limit=10
    )
    assert seen == []

    later = timezone.now() + timedelta(seconds=10)
    seen = pull_all(
        registry.ITEMS,
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        limit=10,
        now=later,
    )
    assert [one["name"] for one in seen] == ["Recién escrito"]


def test_a_deactivated_item_arrives_as_a_departure(tenant_a, sede_a):
    """Criterion 14 · a row that left the predicate is served with a deletion
    marker, computed from the predicate at read time. It is not hard-deleted,
    which is the only reason there is a row left to evaluate."""
    item = make_item(tenant_a, "Se desactiva")
    later = timezone.now() + timedelta(seconds=10)
    seen = pull_all(
        registry.ITEMS, tenant_id=tenant_a.id, location_id=sede_a.id, now=later
    )
    assert seen[0]["_deleted"] is False

    item.active = False
    item.save(update_fields=["active", "updated_at"])
    seen = pull_all(
        registry.ITEMS,
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        now=timezone.now() + timedelta(seconds=10),
    )
    departure = [one for one in seen if one["id"] == str(item.id)][-1]
    assert departure["_deleted"] is True


def test_a_barcode_whose_item_was_deactivated_departs_too(tenant_a, sede_a):
    """The membership rule is a join, and it is evaluated over the page rather
    than in the scan -- a WHERE that excluded the row would leave the barcode on
    every till forever."""
    from core.models import ItemBarcode

    item = make_item(tenant_a, "Con código")
    ItemBarcode.objects.create(tenant=tenant_a, item=item, code="7701234567890")
    item.active = False
    item.save(update_fields=["active", "updated_at"])

    seen = pull_all(
        registry.BARCODES,
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        now=timezone.now() + timedelta(seconds=10),
    )
    assert [one["_deleted"] for one in seen] == [True]


def test_prices_are_this_sede_s_and_the_network_s_and_no_other_sede_s(tenant_a, sede_a):
    """A4 · the till holds one sede's operating set. A price scoped to Suba is
    not served to a Chapinero device at all -- not as a row and not as a
    departure, because it was never inside this device's scope."""
    suba = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a, "Losartán 50 mg")
    today = timezone.localdate()
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        location=None,
        price="4200.00",
        effective_from=today,
        source=PriceSource.IMPORTED,
    )
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

    later = timezone.now() + timedelta(seconds=10)
    chapinero = pull_all(
        registry.PRICES, tenant_id=tenant_a.id, location_id=sede_a.id, now=later
    )
    assert sorted(one["price"] for one in chapinero) == ["3900.00", "4200.00"]

    at_suba = pull_all(
        registry.PRICES, tenant_id=tenant_a.id, location_id=suba.id, now=later
    )
    assert sorted(one["price"] for one in at_suba) == ["4200.00", "5100.00"]


def test_a_price_whose_window_closed_departs(tenant_a, sede_a):
    item = make_item(tenant_a, "Repreciado")
    today = timezone.localdate()
    price = ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        location=None,
        price="4200.00",
        effective_from=today - timedelta(days=30),
        source=PriceSource.IMPORTED,
    )
    price.effective_to = today
    price.save(update_fields=["effective_to", "updated_at"])

    seen = pull_all(
        registry.PRICES,
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        now=timezone.now() + timedelta(seconds=10),
    )
    assert [one["_deleted"] for one in seen] == [True]


def test_half_a_cursor_is_refused_rather_than_defaulted(tenant_a):
    """`updated_at` with no `id` reopens the same-instant hole the tuple closes,
    so it is a 422 and not a silent fallback."""
    from ninja.errors import HttpError

    with pytest.raises(HttpError):
        pull.parse_cursor(timezone.now().isoformat(), None)
    with pytest.raises(HttpError):
        pull.parse_cursor(None, str(uuid.uuid4()))
    assert pull.parse_cursor(None, None).is_start


def test_the_digest_disagrees_when_the_local_store_is_missing_a_row(tenant_a, sede_a):
    """Criterion 28 · the backstop that makes the horizon an engineering choice
    rather than a bet. A digest that agrees while the till is missing a row is
    the worse of the two failures, because it removes the backstop rather than
    the row."""
    for index in range(3):
        make_item(tenant_a, f"Producto {index}")
    ceiling = timezone.now() + timedelta(seconds=10)
    served = pull_all(
        registry.ITEMS, tenant_id=tenant_a.id, location_id=sede_a.id, now=ceiling
    )

    answer = digest.build(
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        cursor_limit=ceiling,
        options=options(),
    )
    assert answer["items"]["count"] == 3

    import hashlib

    local = hashlib.sha256()
    for document in sorted(served, key=lambda one: (one["updated_at"], one["id"])):
        local.update(f"{document['id']}:{document['updated_at']}\n".encode("utf-8"))
    assert local.hexdigest() == answer["items"]["checksum"], (
        "the digest hashes the same strings the pull sends, or it is a "
        "permanent false mismatch that re-pulls every collection every day"
    )

    # One row short locally, and the digest has to say so.
    short = hashlib.sha256()
    for document in sorted(served, key=lambda one: (one["updated_at"], one["id"]))[:-1]:
        short.update(f"{document['id']}:{document['updated_at']}\n".encode("utf-8"))
    assert short.hexdigest() != answer["items"]["checksum"]
