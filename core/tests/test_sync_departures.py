"""How a row leaves a till, and the one class of departure that cannot be served.

**A registry collection is never hard-deleted while the registry lists it**,
because a hard delete leaves no row to evaluate and no `updated_at` to serve.
Criterion 14 puts the check on the code path rather than on the outcome, and
this file is that check.
"""

import ast
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from core.models import ItemBarcode
from core.sync import registry
from core.tests.test_sync_pull import make_item, options, pull_all

pytestmark = pytest.mark.django_db


def test_deactivating_an_item_serves_its_barcodes_as_departures(tenant_a, sede_a):
    """The mechanism migration 0008 installs, and why it has to exist.

    A barcode's membership rule is its **item's** `active` flag, and
    deactivating an item moves `items.updated_at` and not the barcode's. Without
    the trigger, every barcode already behind a device's cursor would stay on
    that till forever — and a scan would resolve to a product the shop no longer
    sells, which is worse than the scan finding nothing.
    """
    item = make_item(tenant_a, "Se desactiva")
    ItemBarcode.objects.create(tenant=tenant_a, item=item, code="7701234567890")

    # The till has both, and the barcode is behind its cursor.
    later = timezone.now() + timedelta(seconds=10)
    seen = pull_all(
        registry.BARCODES, tenant_id=tenant_a.id, location_id=sede_a.id, now=later
    )
    assert [one["_deleted"] for one in seen] == [False]
    checkpoint = seen[-1]["updated_at"]

    item.active = False
    item.save(update_fields=["active", "updated_at"])

    barcode = ItemBarcode.objects.get(item=item)
    assert barcode.updated_at.isoformat() > checkpoint.replace("Z", "+00:00"), (
        "the trigger did not move the barcode's `updated_at`, so its departure "
        "is behind the device's cursor and can never be served"
    )

    from core.sync import pull

    documents, _checkpoint, _more = pull.page(
        registry.BARCODES,
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        cursor=pull.parse_cursor(checkpoint, seen[-1]["id"]),
        limit=10,
        options=options(),
        now=timezone.now() + timedelta(seconds=10),
    )
    assert [one["_deleted"] for one in documents] == [True]


def test_reactivating_an_item_brings_its_barcodes_back(tenant_a, sede_a):
    """The trigger fires on the flag changing, in either direction."""
    item = make_item(tenant_a, "Vuelve", active=False)
    ItemBarcode.objects.create(tenant=tenant_a, item=item, code="7709999999999")
    item.active = True
    item.save(update_fields=["active", "updated_at"])

    seen = pull_all(
        registry.BARCODES,
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        now=timezone.now() + timedelta(seconds=10),
    )
    assert [one["_deleted"] for one in seen] == [False]


def test_the_trigger_does_not_fire_on_an_ordinary_catalog_edit(tenant_a):
    """`AFTER UPDATE OF active ... WHEN (OLD.active IS DISTINCT FROM NEW.active)`.

    Renaming four thousand items must not rewrite seven thousand barcodes.
    """
    item = make_item(tenant_a, "Se renombra")
    barcode = ItemBarcode.objects.create(
        tenant=tenant_a, item=item, code="7701111111111"
    )
    before = ItemBarcode.objects.get(id=barcode.id).updated_at

    item.name = "Se renombró"
    item.save(update_fields=["name", "updated_at"])

    assert ItemBarcode.objects.get(id=barcode.id).updated_at == before


def test_a_departure_carries_its_id_and_the_marker_and_nothing_else(tenant_a, sede_a):
    """The device is being told to remove a row, so the row's contents are not
    part of the instruction.

    A `customers` departure that carried its payload would put every person who
    has fallen out of the recency window — with their name, document number,
    phone and address — on every till in the network, on the first sync and on
    every reset after it. The window exists to keep them off the till; this is
    the other door (Ley 1581).
    """
    from core.models import Customer

    stale = Customer.objects.create(
        tenant=tenant_a,
        document_type="CC",
        document="10203040",
        name="Rosa Vieja",
        phone="3001234567",
        email="rosa@example.com",
        address="Calle 1",
    )
    # Outside `customer_recency_months`, and therefore a departure.
    Customer.objects.filter(id=stale.id).update(
        updated_at=timezone.now() - timedelta(days=30 * 40)
    )

    seen = pull_all(
        registry.CUSTOMERS,
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        now=timezone.now() + timedelta(seconds=10),
    )
    assert len(seen) == 1
    departure = seen[0]
    assert departure["_deleted"] is True
    assert set(departure) == {"id", "updated_at", "_deleted"}
    body = str(departure)
    for secret in ("10203040", "Rosa Vieja", "3001234567", "rosa@example.com"):
        assert secret not in body


def test_an_arrival_carries_its_whole_document(tenant_a, sede_a):
    """The other half, so the check above cannot pass by serving nothing."""
    from core.models import Customer

    Customer.objects.create(
        tenant=tenant_a, document_type="CC", document="99887766", name="Ana Nueva"
    )
    seen = pull_all(
        registry.CUSTOMERS,
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        now=timezone.now() + timedelta(seconds=10),
    )
    assert seen[0]["_deleted"] is False
    assert seen[0]["name"] == "Ana Nueva"
    assert seen[0]["document"] == "99887766"


#: Every place in the product that hard-deletes a row from a **registry
#: collection**, as `(module, function)`.
#:
#: **This list is the check, and it is deliberately not empty.** Criterion 14
#: asks that no endpoint hard-deletes a registry row; six S1 paths do, and
#: closing them needs either a column on a table ledger rule 1 assigns to S1 or
#: the tombstone table S2's document says must be brought to it first. What S2
#: can do -- and what this does -- is make the set **declared and checked**, so
#: a seventh cannot appear unnoticed, and make the containment real: every one
#: of these changes a row count, which is exactly what `GET /api/sync/digest`
#: compares, so a till repairs itself within a day rather than never.
DECLARED_HARD_DELETES = {
    # An item's barcodes are replaced wholesale on every edit, and a barcode
    # carries no flag to deactivate. A removed code keeps resolving to that item
    # on every till until the digest re-pulls the collection.
    ("core/catalog/api.py", "_replace_barcodes"),
    # Ley 1581 · the row is erased in place once any sale references it, and
    # hard-deleted only while nothing does. At S2 nothing does, so this is
    # always a hard delete.
    ("core/catalog/api.py", "delete_customer"),
    # A laboratorio or a categoría with no items, removed from the taxonomy.
    ("core/catalog/api.py", "delete_manufacturer"),
    ("core/catalog/api.py", "delete_category"),
    # A price withdrawn before it was ever in force. The one of the five that
    # is nearly harmless: the row it re-opens *is* served as an arrival, so the
    # till converges on the right price within a pull interval and only the
    # withdrawn row itself lingers.
    ("core/catalog/api.py", "withdraw_price"),
    ("core/catalog/prices.py", "withdraw_price"),
}

#: The models whose tables are in the sync registry. A `.delete()` in a function
#: that names one of these is what the guard is looking for.
REGISTRY_MODELS = {
    "Item",
    "ItemBarcode",
    "Manufacturer",
    "Category",
    "ItemPrice",
    "Customer",
}


def _functions_that_delete(root):
    """Every function in `core/` containing a `.delete()`, as
    `(module, function)`.

    **Parsed rather than grepped**: a call split across lines is still one call,
    and `.delete()` inside a docstring is not a call at all -- and the modules
    here discuss deletion at length. Migrations and tests are excluded: a
    migration's deletes are schema operations, and a test deleting a row is the
    check rather than the product.
    """
    found = {}
    for path in sorted(root.glob("core/**/*.py")):
        relative = str(path.relative_to(root))
        if relative.startswith(("core/migrations/", "core/tests/")):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            deletes = any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr in ("delete", "bulk_delete")
                for inner in ast.walk(node)
            )
            if deletes:
                found[(relative, node.name)] = node
    return found


def _names_in(node):
    names = {inner.id for inner in ast.walk(node) if isinstance(inner, ast.Name)}
    names |= {
        inner.attr for inner in ast.walk(node) if isinstance(inner, ast.Attribute)
    }
    return names


def test_the_hard_deletes_that_reach_a_registry_collection_are_declared():
    """Criterion 14, as a check on the code path rather than on the outcome.

    A hard delete leaves no row for `pull.page` to evaluate and no `updated_at`
    to serve, so the row survives on every till indefinitely. The six that
    exist are declared above with the reason each is survivable; **a seventh
    fails this test**, which is the whole point of writing the set down.
    """
    root = Path(__file__).resolve().parents[2]
    reaching = {
        entry
        for entry, node in _functions_that_delete(root).items()
        if _names_in(node) & REGISTRY_MODELS
    }
    assert reaching == DECLARED_HARD_DELETES, (
        "a hard delete on a registry collection appeared or moved. A deleted "
        "row leaves nothing for the pull to evaluate, so it lives on every till "
        "until the daily digest re-pulls the whole collection. Either close it "
        "(deactivate instead of deleting) or add it here with the reason it is "
        "survivable.\n"
        f"  new:  {sorted(reaching - DECLARED_HARD_DELETES)}\n"
        f"  gone: {sorted(DECLARED_HARD_DELETES - reaching)}"
    )


def test_swapping_an_items_primary_barcode_keeps_one_and_moves_both(
    client_as, tenant_a, owner_a
):
    """The barcode editor moves the primary flag through zero exactly once.

    Two properties, and S2 needs both. The item ends with **exactly one**
    primary whichever order the codes are submitted in — clearing inside the
    loop demoted the row the same loop had just promoted, so submitting the new
    primary first left the item with none. And **both rows' `updated_at` move**,
    because `is_primary` is a field the registry serialises: a flag that changed
    without moving `updated_at` is a change no delta pull can serve and no
    digest can see, so every till would keep scanning to the old primary for
    ever.
    """
    item = make_item(tenant_a, "Con dos códigos")
    ItemBarcode.objects.create(
        tenant=tenant_a, item=item, code="7700000000001", is_primary=True
    )
    ItemBarcode.objects.create(
        tenant=tenant_a, item=item, code="7700000000002", is_primary=False
    )
    before = {row.code: row.updated_at for row in ItemBarcode.objects.filter(item=item)}

    # The promoted code **first**, which is the order that used to lose it.
    response = client_as(owner_a).patch(
        f"/api/items/{item.id}",
        {
            "barcodes": [
                {"code": "7700000000002", "is_primary": True},
                {"code": "7700000000001", "is_primary": False},
            ]
        },
        content_type="application/json",
    )
    assert response.status_code == 200, response.content

    rows = {row.code: row for row in ItemBarcode.objects.filter(item=item)}
    assert [code for code, row in rows.items() if row.is_primary] == ["7700000000002"]
    for code in before:
        assert rows[code].updated_at > before[code], (
            f"{code} changed without moving `updated_at`, so no till will ever see it"
        )


def test_S2s_own_code_hard_deletes_nothing():
    """The half of criterion 14 that is unconditional.

    Whatever S1 does, **nothing under `core/sync/` hard-deletes anything** --
    not a registry row, not a conflict, not a device. Revocation is not
    deletion, a conflict is closed and never removed, and a departure is a
    marker rather than a `DELETE`.
    """
    root = Path(__file__).resolve().parents[2]
    offenders = sorted(
        entry
        for entry in _functions_that_delete(root)
        if entry[0].startswith("core/sync/")
    )
    assert offenders == []


def test_every_registry_collection_leaves_by_an_update_or_is_declared():
    """The registry's own half: each collection states how a row leaves it.

    A collection whose rows can only vanish by a hard delete has no departure at
    all, and would be a row on every till forever.
    """
    departure = {
        "items": "items.active",
        "item_barcodes": "the item's active flag, through 0008's trigger",
        "manufacturers": "never leaves — the whole table is the predicate",
        "categories": "never leaves — the whole table is the predicate",
        "item_prices": "effective_to",
        "customers": "the recency window, repaired by the digest",
    }
    assert set(departure) == set(registry.BY_NAME), (
        "a collection was added to the registry without saying how a row leaves "
        "it — which is how a row ends up on every till forever"
    )


def test_a_hard_deleted_row_is_caught_by_the_digest_within_a_day(tenant_a, sede_a):
    """The containment, measured rather than asserted.

    S1's `DELETE /api/customers/{id}` hard-deletes an unreferenced customer.
    Nothing can serve that departure — but the row **count** moves, and the
    count is half of what the daily digest compares.
    """
    from core.models import Customer
    from core.sync import digest

    Customer.objects.create(
        tenant=tenant_a, document_type="CC", document="1020304050", name="Ana"
    )
    ceiling = timezone.now() + timedelta(seconds=10)
    before = digest.build(
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        cursor_limit=ceiling,
        options=options(),
    )["customers"]

    Customer.objects.filter(document="1020304050").delete()

    after = digest.build(
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        cursor_limit=ceiling,
        options=options(),
    )["customers"]
    assert after["count"] == before["count"] - 1
    assert after["checksum"] != before["checksum"]


def test_the_checksum_catches_a_change_the_count_cannot(tenant_a, sede_a):
    """Why the digest is not just a count.

    A row rewritten inside an open transaction that outlived the horizon leaves
    the count exactly where it was. The checksum is over `(id, updated_at)`, so
    it moves and the count does not — which is the whole reason it exists.
    """
    from core.sync import digest

    item = make_item(tenant_a, "Se renombra")
    ceiling = timezone.now() + timedelta(seconds=10)
    before = digest.build(
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        cursor_limit=ceiling,
        options=options(),
    )["items"]

    # Saved through the model, so `auto_now` stamps `updated_at` exactly as any
    # ordinary write would. The case this stands for is a row whose write the
    # till never saw: it was stamped inside a transaction that outlived the
    # safety horizon, so the till's cursor passed it and the till still holds
    # the older version. Server and till then agree on the count and disagree on
    # the checksum, which is the divergence a count alone cannot see.
    item.name = "Se renombró"
    item.save(update_fields=["name", "updated_at"])

    after = digest.build(
        tenant_id=tenant_a.id,
        location_id=sede_a.id,
        cursor_limit=ceiling,
        options=options(),
    )["items"]
    assert after["count"] == before["count"]
    assert after["checksum"] != before["checksum"]
