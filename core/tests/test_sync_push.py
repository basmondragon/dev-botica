"""The idempotent client-write service (rule 8, A5).

The five properties in S2's *Offline* section are the vocabulary here: a dedupe
that returns `applied` twice, a batch rejection drawn on the wrong line, an
`occurred_at` that was corrected, or a push carrying a projection.
"""

import uuid

import pytest
from django.utils import timezone

from core.models import Customer, SyncConflict, SyncConflictType
from core.sync import push, registry
from core.sync import settings as sync_settings
from core.tenancy import ForeignTenantRow, pin_tenant
from core.tests.conftest import make_location
from core.tests.test_sync_pull import make_device

pytestmark = pytest.mark.django_db


def options():
    return dict(sync_settings.DEFAULTS)


def customer_row(document, name="Ana Gómez", row_id=None, **payload):
    """What a till sends: its own row id, so a replay is a `duplicate` and not a
    merge onto a row the client already thinks it has."""
    return {
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


def apply(device, rows, batch_id="batch-1"):
    with pin_tenant(device.tenant_id):
        return push.apply_batch(
            device, batch_id, rows, options=options(), request_id="req_test"
        )


def test_a_customer_registered_at_the_counter_arrives_once(tenant_a, sede_a):
    """§5 rule 3 · the same batch replayed produces one row and returns
    `duplicate` with the same id. The property holds when the network failed
    **after** the server committed, which is the only case that matters."""
    device, _key = make_device(tenant_a, sede_a)
    row = customer_row("1020304050")

    first = apply(device, [row])
    assert [one.outcome for one in first.outcomes] == [push.APPLIED]
    assert Customer.objects.filter(tenant=tenant_a, document="1020304050").count() == 1

    # The same batch, byte for byte: a push that timed out after the commit.
    again = apply(device, [row])
    assert again.outcomes[0].outcome == push.DUPLICATE
    assert again.outcomes[0].id == first.outcomes[0].id
    assert Customer.objects.filter(tenant=tenant_a, document="1020304050").count() == 1


def test_two_tills_registering_one_person_converge_on_one_row(tenant_a, sede_a):
    """Criterion 12 · both push, one row exists, both tills converge on its id,
    and neither cashier is shown an error."""
    suba = make_location(tenant_a, "SUB", "Suba")
    chapinero_till, _ = make_device(tenant_a, sede_a)
    suba_till, _ = make_device(tenant_a, suba)

    first = apply(chapinero_till, [customer_row("1098765432", "Ana Gómez")])
    second = apply(suba_till, [customer_row("1098765432", "Ana G.")])

    assert first.outcomes[0].outcome == push.APPLIED
    assert second.outcomes[0].outcome == push.MERGED
    assert second.outcomes[0].id == first.outcomes[0].id
    assert Customer.objects.filter(tenant=tenant_a, document="1098765432").count() == 1


def test_a_merge_touches_the_row_so_it_reaches_the_till_that_merged(tenant_a, sede_a):
    """What makes `merged` end with the cashier looking at the customer they
    meant.

    The till's own row is removed on a merge — the client does not copy a
    hurried counter entry over a record the office already has. So the server's
    row has to **arrive**, and a customer last updated three years ago is
    outside `customer_recency_months` and would never be served. Touching
    `updated_at` puts them inside it, which is not a trick: they were just seen
    at a counter, which is exactly what recency means.

    Nothing else on the row moves.
    """
    device, _key = make_device(tenant_a, sede_a)
    known = Customer.objects.create(
        tenant=tenant_a,
        document_type="CC",
        document="1122334455",
        name="Rosa Antigua",
        phone="3009998877",
    )
    Customer.objects.filter(id=known.id).update(
        updated_at=timezone.now() - timezone.timedelta(days=30 * 40)
    )
    before = Customer.objects.get(id=known.id)

    result = apply(device, [customer_row("1122334455", "Rosa A.")])
    assert result.outcomes[0].outcome == push.MERGED
    assert result.outcomes[0].id == str(known.id)

    after = Customer.objects.get(id=known.id)
    assert after.updated_at > before.updated_at
    # The office's record stands: nothing the till typed was written over it.
    assert after.name == "Rosa Antigua"
    assert after.phone == "3009998877"


def test_a_foreign_tenant_row_rejects_the_whole_batch(tenant_a, tenant_b, sede_a):
    """Criterion 11 · no row in the batch is applied. Filtering the foreign row
    out would apply the rest from a client we have just established we cannot
    trust about which tenant it is in."""
    device, _key = make_device(tenant_a, sede_a)
    rows = [
        customer_row("1111111111"),
        {
            **customer_row("2222222222"),
            "payload": {
                "document_type": "CC",
                "document": "2222222222",
                "name": "Otra red",
                "tenant_id": str(tenant_b.id),
            },
        },
        customer_row("3333333333"),
    ]
    with pytest.raises(ForeignTenantRow):
        push.check_provenance(device, rows)
    assert (
        Customer.objects.filter(
            tenant=tenant_a, document__in=["1111111111", "3333333333"]
        ).count()
        == 0
    )


def test_a_foreign_location_row_rejects_the_whole_batch(tenant_a, sede_a):
    device, _key = make_device(tenant_a, sede_a)
    suba = make_location(tenant_a, "SUB", "Suba")
    rows = [
        customer_row("4444444444"),
        {
            **customer_row("5555555555"),
            "payload": {
                "document_type": "CC",
                "document": "5555555555",
                "name": "Otra sede",
                "location_id": str(suba.id),
            },
        },
    ]
    with pytest.raises(push.ForeignLocationRow):
        push.check_provenance(device, rows)


def test_a_bad_row_is_rejected_alone_and_the_rest_drain(tenant_a, sede_a):
    """The other half of the line: one malformed customer must not wedge nine
    good sales behind it."""
    device, _key = make_device(tenant_a, sede_a)
    rows = [
        customer_row("6666666666"),
        customer_row("", name="Sin documento"),
        customer_row("7777777777"),
    ]
    result = apply(device, rows)
    outcomes = {one.outcome for one in result.outcomes}
    assert outcomes == {push.APPLIED, push.REJECTED}
    assert (
        Customer.objects.filter(
            tenant=tenant_a, document__in=["6666666666", "7777777777"]
        ).count()
        == 2
    )


def test_a_rejection_raises_a_conflict_carrying_no_document_number(tenant_a, sede_a):
    """Ley 1581 · `detail` carries the collection, the failing field, the reason
    code and the correlation id, and never the payload."""
    device, _key = make_device(tenant_a, sede_a)
    apply(device, [customer_row("8888888888", name="")])

    with pin_tenant(tenant_a.id):
        row = SyncConflict.objects.get(type=SyncConflictType.PAYLOAD_REJECTED)
    assert row.detail == {
        "reason": "name_required",
        "field": "name",
        "request_id": "req_test",
    }
    assert "8888888888" not in str(row.detail)
    assert row.collection == "customers"
    assert row.device_id == device.id


def test_an_unknown_collection_is_a_row_rejection_not_a_batch_one(tenant_a, sede_a):
    """A client out of date about the registry is not a client wrong about which
    network it is in, so it is refused per row."""
    device, _key = make_device(tenant_a, sede_a)
    rows = [
        {
            "collection": "stock_moves",
            "client_uuid": str(uuid.uuid4()),
            "occurred_at": None,
            "payload": {},
        },
        customer_row("9999999999"),
    ]
    result = apply(device, rows)
    by_outcome = {one.outcome for one in result.outcomes}
    assert by_outcome == {push.REJECTED, push.APPLIED}
    with pin_tenant(tenant_a.id):
        assert SyncConflict.objects.filter(
            type=SyncConflictType.UNKNOWN_COLLECTION
        ).exists()


def test_a_reference_collection_is_not_pushable(tenant_a, sede_a):
    """§5 rule 5's enforcement is the registry's `push` column, not a review.

    A stage that wants to push a projection is refused here, before it has
    written a handler.
    """
    with pytest.raises(registry.Unpushable):
        registry.pushable("items")
    with pytest.raises(registry.Unpushable):
        registry.pushable("item_prices")
    assert registry.pushable("customers").natural_key == ("document_type", "document")


def test_both_caps_refuse_a_batch_whole(tenant_a, sede_a):
    """Splitting is the client's job. A server that silently halved a batch
    would make the outbox and the response disagree about what was sent."""
    small = {**options(), "push_batch_max_rows": 2}
    with pytest.raises(push.BatchTooLarge):
        push.check_size([customer_row(str(index)) for index in range(3)], small)

    tight = {**options(), "push_batch_max_bytes": 10}
    with pytest.raises(push.BatchTooLarge):
        push.check_size([customer_row("1")], tight)
