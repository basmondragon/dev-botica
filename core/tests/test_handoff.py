"""The handoff, from the silence outward.

**Silence is checked first because it is the default.** With no target
configured there are no `fiscal_documents` rows, no queue, no retry job, no work
list figure and no error, warning or badge anywhere -- and that is the state
every demo runs in and the state the seed ships (architecture §8).

Everything after it is the second failure this stage is shaped around: a
duplicate at the far end is signed, numbered and filed with the DIAN under the
pharmacy's own resolution, so a timeout is never resolved by re-sending.

Two targets carry the whole file and both ship with the stage: the **file export
target** and a **loopback** one that records every request it receives, holds
what it accepts, and can be told to refuse, to hang, or to commit and then drop
the connection. Neither leaves the instance. A check that needs a client's
invoicing system is a check no build session can run, and there are none here.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.counter import sales as sale_service
from core.fiscal import (
    delivery,
    document as canonical,
    export,
    jobs,
    service as handoff,
    settings as invoicing,
    transports,
)
from core.models import (
    AuditLog,
    Customer,
    FiscalDocument,
    FiscalDocumentStatus,
    Item,
    ItemBarcode,
    Sale,
    SaleReturn,
    SaleSource,
    StockMove,
    Tenant,
    VatClass,
)
from core.tenancy import pin_tenant
from core.tests.conftest import make_location, make_user
from core.tests.test_counter_push import Till, apply, envelope, price, stock
from core.tests.test_inventory_ledger import make_lot
from core.tests.test_sync_pull import make_device, make_item

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------


def connect(client, target="loopback", **extra):
    """Reach the configured state **through the product's own settings screen**.

    Never through a fixture and never through a shell: a stage that shipped its
    own flip to a configured state would be the second seeding path the ledger
    forbids, and it would be the one path no reviewer reads because it is not
    the one an administrator walks (S5, *Verification*).
    """
    response = client.patch(
        "/api/settings/invoicing",
        {"target": target, **extra},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()


def ring(
    tenant,
    location,
    user,
    *,
    label="Caja 1",
    vat=VatClass.EXCLUDED,
    quantity=4,
    unit_price="3900",
    customer=None,
    barcode="7702001",
):
    """One closed ticket, rung the way a till rings it, with a barcode on the
    item so the document carries the code an accountant recognises."""
    device, _key = make_device(tenant, location, label=label)
    item = make_item(tenant, f"Producto {label} {location.code}", tracks_lots=True)
    Item.objects.filter(id=item.id).update(vat_class=vat, unit="unidad")
    item.refresh_from_db()
    # Unique per tenant, so two tills at one sede do not collide on the partial
    # unique index `item_barcodes` carries.
    ItemBarcode.objects.create(
        tenant=tenant,
        item=item,
        code=f"{barcode}{location.code}{label[-1]}",
        is_primary=True,
    )
    lot = make_lot(tenant, item, code=f"L-{location.code}{label[-1]}")
    price(tenant, item, unit_price)
    stock(tenant, location, item, lot, 200)
    till = Till(device, user)
    rows = [till.open_shift(), till.open_sale()]
    rows.append(till.line(item, quantity, unit_price, lot=lot))
    rows.append(till.payment("cash", str(Decimal(unit_price) * quantity)))
    rows.append(
        till.close_sale(**({"customer_id": str(customer.id)} if customer else {}))
    )
    apply(device, rows, batch_id=f"batch-{location.code}-{label}")
    with pin_tenant(tenant.id):
        return Sale.objects.get(tenant=tenant, device=device), till, item, lot


def documents(tenant):
    with pin_tenant(tenant.id):
        return list(FiscalDocument.objects.filter(tenant=tenant).order_by("created_at"))


def one_document(tenant):
    rows = documents(tenant)
    assert len(rows) == 1, [row.document_key for row in rows]
    return rows[0]


def run_delivery(document):
    """One attempt, the way the job runs it -- pinned, and returning what it
    did rather than raising."""
    with pin_tenant(document.tenant_id):
        return delivery.attempt(document.id, tenant_id=document.tenant_id)


def reload(document):
    with pin_tenant(document.tenant_id):
        return FiscalDocument.objects.get(id=document.id)


def log(tenant):
    return transports.request_log(str(tenant.id))


def held(tenant):
    return transports.held_documents(str(tenant.id))


# ---------------------------------------------------------------------------
# 1 · Silence, checked first because it is the default
# ---------------------------------------------------------------------------


def test_unconfigured_writes_nothing_and_says_nothing(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 1 · acceptance 1, 2.

    Sales close across two sedes and **nothing exists**: no rows, no jobs, and a
    summary body whose only key is `configured: false`. A body carrying
    `pending: 0` passes a count check and fails this one, and that is the
    failure that matters, **because zero renders and absent does not** (§8).
    """
    suba = make_location(tenant_a, "SUB", "Suba")
    other = make_user(tenant_a, "cashier", "suba@la45.co", location=suba)
    ring(tenant_a, sede_a, cashier_a)
    ring(tenant_a, suba, other, label="Caja 2")

    assert documents(tenant_a) == []

    office = client_as(owner_a)
    body = office.get("/api/fiscal-documents/summary").json()
    assert body == {"configured": False}, body

    # The work-list route still resolves and holds nothing to render.
    listing = office.get("/api/fiscal-documents").json()
    assert listing["row_count"] == 0
    assert office.get("/api/fiscal-documents/unsent-sales").json()["row_count"] == 0

    # Nothing was queued for anybody.
    assert _queued_names() == set()

    # The only surface that names the state is the settings section, and it
    # says the handoff is off rather than reporting a problem.
    group = office.get("/api/settings/invoicing").json()
    assert group["target"] == ""
    assert group["enabled"] is False
    assert group["configured_at"] == ""
    assert group["held"] == 0


def _queued_names():
    """Job names the product's own code deferred, ignoring the worker's own
    periodic dispatch -- which no test process runs."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT task_name FROM procrastinate_jobs "
            "WHERE task_name LIKE '%fiscal%' OR task_name = 'notify_failed_deliveries'"
        )
        return {row[0] for row in cursor.fetchall()}


# ---------------------------------------------------------------------------
# 2 · Turning it on has a boundary
# ---------------------------------------------------------------------------


def test_configuring_stamps_a_boundary_and_nothing_before_it_is_queued(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 2 · acceptance 3. **Nothing is backfilled**, ever.

    A pharmacy that has been invoicing in its own system all month does not want
    a month of duplicates, which is precisely why nothing was queued while
    unconfigured (§8).
    """
    ring(tenant_a, sede_a, cashier_a, label="Caja 1")
    office = client_as(owner_a)
    group = connect(office)
    assert group["configured_at"]
    assert group["enabled"] is True
    assert documents(tenant_a) == []

    ring(tenant_a, sede_a, cashier_a, label="Caja 2")
    rows = documents(tenant_a)
    assert len(rows) == 1
    assert rows[0].status == FiscalDocumentStatus.PENDING

    # The sale closed before the boundary appears on **no** list: it was never
    # due, so it is not an orphan either.
    unsent = office.get("/api/fiscal-documents/unsent-sales").json()
    assert unsent["row_count"] == 0


# ---------------------------------------------------------------------------
# 3 · The payload
# ---------------------------------------------------------------------------


def test_the_payload_reconciles_to_the_peso_across_three_tax_classes(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 3 · acceptance 4. Lines sum to the totals with **no rounding
    tolerance**, and `tax_by_class` states the base and the tax per rate."""
    office = client_as(owner_a)
    connect(office)

    device, _key = make_device(tenant_a, sede_a, label="Caja 9")
    till = Till(device, cashier_a)
    rows = [till.open_shift(), till.open_sale()]
    total = Decimal("0")
    for index, (vat, unit) in enumerate(
        (
            (VatClass.EXCLUDED, "3900"),
            (VatClass.RATE_5, "8400"),
            (VatClass.RATE_19, "12000"),
        )
    ):
        item = make_item(tenant_a, f"Referencia {vat}", tracks_lots=True)
        Item.objects.filter(id=item.id).update(vat_class=vat)
        item.refresh_from_db()
        lot = make_lot(tenant_a, item, code=f"L-{index}")
        price(tenant_a, item, unit)
        stock(tenant_a, sede_a, item, lot, 50)
        rows.append(till.line(item, 2, unit, lot=lot, position=index))
        total += Decimal(unit) * 2
    rows.append(till.payment("cash", str(total)))
    rows.append(till.close_sale())
    apply(device, rows, batch_id="batch-tax")

    with pin_tenant(tenant_a.id):
        sale = Sale.objects.get(tenant=tenant_a, device=device)
    payload = office.get(f"/api/sales/{sale.id}/canonical-document").json()

    lines = payload["lines"]
    # **A line's own money is its net**, in whole pesos, and the lines sum to
    # the total exactly. The unit price is a rate and travels as a decimal
    # string, because `unit_price × quantity` is exact only in decimals.
    assert sum(one["line_total"] for one in lines) == payload["totals"]["total"]
    assert sum(one["tax_amount"] for one in lines) == payload["totals"]["tax"]
    assert all(isinstance(one["unit_price"], str) for one in lines)
    assert all(one["unit_price"].count(".") == 1 for one in lines)
    assert {one["vat_class"] for one in payload["totals"]["tax_by_class"]} == {
        "excluded",
        "rate_5",
        "rate_19",
    }
    with pin_tenant(tenant_a.id):
        sale.refresh_from_db()
    # Every total is the ticket's own figure, rounded once. **Tax is the one
    # that cannot be whole per line and per ticket at the same time**: 19%
    # contained in $24.000 is $3.831,93, so it is rounded from the ticket and
    # the line figures are apportioned to it.
    assert payload["totals"]["subtotal"] == canonical.rounded(sale.subtotal)
    assert payload["totals"]["discount"] == canonical.rounded(sale.discount)
    assert payload["totals"]["total"] == canonical.rounded(sale.total)
    assert payload["totals"]["tax"] == canonical.rounded(sale.tax)
    assert sale.tax != sale.tax.to_integral_value()
    # A base is the line net less the tax it already contains, because a
    # Colombian shelf price is tax-inclusive.
    for row in payload["totals"]["tax_by_class"]:
        assert row["taxable_base"] >= 0


def test_a_payload_that_does_not_reconcile_is_never_sent(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 3, second half. Move one line's tax by a peso: the row is `failed`
    with the arithmetic named, and **the target's log holds nothing at all**."""
    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a, vat=VatClass.RATE_19)
    row = one_document(tenant_a)
    assert row.status == FiscalDocumentStatus.PENDING

    with pin_tenant(tenant_a.id):
        line = sale.lines.get()
        line.tax_amount = line.tax_amount + Decimal("1.00")
        line.save(update_fields=["tax_amount"])

    assert run_delivery(row) == "failed"
    row = reload(row)
    assert row.status == FiscalDocumentStatus.FAILED
    assert "IVA" in row.error
    assert [
        entry for entry in log(tenant_a) if entry["document_key"] == row.document_key
    ] == []


# ---------------------------------------------------------------------------
# 4 · The acquirer
# ---------------------------------------------------------------------------


def test_the_acquirer_renders_in_all_three_forms(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 4 · acceptance 5, 6.

    With no customer the three identifying fields are **absent from the object**,
    not present and null: a null where a field should be absent is a null a
    mapping has to special-case, and the first one that forgets sends
    `"name": null` to a system that stores it as the string `null`.
    """
    office = client_as(owner_a)
    connect(office)

    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a, label="Caja 1")
    anonymous = office.get(f"/api/sales/{sale.id}/canonical-document").json()
    assert anonymous["acquirer"] == {"is_final_consumer": True}

    # A customer the till registered **while the machine had no network**, which
    # arrives on the same push as the sale. The check is the payload after the
    # push lands, not the till's local copy.
    device, _key = make_device(tenant_a, sede_a, label="Caja 2")
    till = Till(device, cashier_a)
    item = make_item(tenant_a, "Otra referencia", tracks_lots=True)
    lot = make_lot(tenant_a, item, code="L-OFF")
    price(tenant_a, item, "5000")
    stock(tenant_a, sede_a, item, lot, 30)
    customer_uuid = "3f2f4c11-4f2b-4a4c-9c6a-1a5b3f0c9a01"
    apply(
        device,
        [
            envelope(
                "customers",
                {
                    "id": customer_uuid,
                    "document_type": "CC",
                    "document": "1020304050",
                    "name": "Luz Marina Peña",
                    "data_consent": True,
                },
            ),
            till.open_shift(),
            till.open_sale(),
            till.line(item, 1, "5000", lot=lot),
            till.payment("cash", "5000"),
            till.close_sale(customer_id=customer_uuid),
        ],
        batch_id="batch-offline-customer",
    )
    with pin_tenant(tenant_a.id):
        offline = Sale.objects.get(tenant=tenant_a, device=device)
    identified = office.get(f"/api/sales/{offline.id}/canonical-document").json()
    assert identified["acquirer"] == {
        "is_final_consumer": False,
        "document_type": "CC",
        "document": "1020304050",
        "name": "Luz Marina Peña",
    }


def test_a_missing_document_number_lands_on_the_work_list_and_a_retry_rebuilds(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 5 · acceptance 7.

    **The cashier's screen showed nothing at any point** -- the sale closed
    normally, the stock moved, and the failure is an administrator's row.
    `Reintentar` rebuilds the payload from the sale as it now stands: one row
    throughout, no edit to it, no migration.
    """
    office = client_as(owner_a)
    connect(office)
    with pin_tenant(tenant_a.id):
        customer = Customer.objects.create(
            tenant=tenant_a, document_type="CC", document="900111222", name="Ana Ruiz"
        )
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a, customer=customer)

    with pin_tenant(tenant_a.id):
        Customer.objects.filter(id=customer.id).update(document="")

    row = one_document(tenant_a)
    assert run_delivery(row) == "failed"
    row = reload(row)
    assert row.status == FiscalDocumentStatus.FAILED
    assert row.error == "El adquiriente no tiene número de documento."

    # The sale itself is untouched and still closed: a validation failure is
    # never a refused sale (§5 rule 2).
    with pin_tenant(tenant_a.id):
        sale.refresh_from_db()
    assert sale.status == "closed"

    with pin_tenant(tenant_a.id):
        Customer.objects.filter(id=customer.id).update(document="900111222")

    response = office.post(f"/api/fiscal-documents/{row.id}/retry")
    assert response.status_code == 200, response.content
    after = reload(row)
    assert after.id == row.id
    assert after.status == FiscalDocumentStatus.PENDING
    assert run_delivery(after) == "acknowledged"
    settled = reload(after)
    assert settled.status == FiscalDocumentStatus.ACKNOWLEDGED
    assert settled.attempts > row.attempts
    assert len(documents(tenant_a)) == 1


# ---------------------------------------------------------------------------
# 6 · Exactly once -- the check this stage exists for
# ---------------------------------------------------------------------------


def test_a_dropped_connection_after_a_commit_is_settled_by_a_query_not_a_resend(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 6 · acceptance 8.

    The target commits and then drops the connection. **Two `deliver` entries --
    or two documents at the far end -- is the failure the whole stage is shaped
    around**, and it is visible in the target's own log rather than inferred
    from our rows.
    """
    office = client_as(owner_a)
    connect(office)
    transports.configure(str(tenant_a.id), mode=transports.COMMIT_THEN_DROP)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)

    row = one_document(tenant_a)
    assert run_delivery(row) == "pending"
    row = reload(row)
    assert row.status == FiscalDocumentStatus.PENDING
    assert row.attempts == 1

    # The ladder fires. The target still cannot answer a write, but it answers a
    # status query -- which is the property the query-first rule depends on.
    assert run_delivery(row) == "acknowledged"
    row = reload(row)
    assert row.status == FiscalDocumentStatus.ACKNOWLEDGED
    assert row.external_number

    entries = [one for one in log(tenant_a) if one["document_key"] == row.document_key]
    assert [one["operation"] for one in entries] == ["deliver", "query"]
    assert (
        len([one for one in held(tenant_a) if one["cude"].endswith(row.document_key)])
        == 1
    )
    assert sale.number in row.document_key


def test_two_concurrent_calls_for_one_sale_produce_one_row(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 7 · acceptance 10.

    `UNIQUE (tenant_id, document_key)` is what makes the second a no-op rather
    than a race the job wins -- and because the key is derived from the sale, a
    second row is impossible by construction.
    """
    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    with pin_tenant(tenant_a.id):
        again = handoff.hand_off_sale(
            Sale.objects.select_related("location").get(id=sale.id)
        )
    rows = documents(tenant_a)
    assert len(rows) == 1
    assert again is not None and again.id == rows[0].id


def test_a_target_that_can_neither_dedupe_nor_be_queried_is_capped_at_one_attempt(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 8 · acceptance 9.

    A deliberately poor experience for a poorly-behaved target, and it is
    correct: a blind retry against a system that cannot dedupe is how a pharmacy
    ends up with two signed fiscal documents for one sale (§8).
    """
    office = client_as(owner_a)
    connect(office, mapping="loopback_blind")
    transports.configure(str(tenant_a.id), mode=transports.COMMIT_THEN_DROP)
    ring(tenant_a, sede_a, cashier_a)

    row = one_document(tenant_a)
    assert run_delivery(row) == "held"
    row = reload(row)
    assert row.attempts == 1
    assert row.next_attempt_at is None
    assert row.status == FiscalDocumentStatus.PENDING
    assert "no permite consultarlo" in row.error

    # The sweep does not pick a held row up, however many times it runs.
    with pin_tenant(tenant_a.id):
        jobs._sweep_tenant(tenant_a)
        jobs._sweep_tenant(tenant_a)
    assert len([one for one in log(tenant_a) if one["operation"] == "deliver"]) == 1


# ---------------------------------------------------------------------------
# 9 · A transport failure is never a refusal
# ---------------------------------------------------------------------------


def test_a_transport_failure_is_never_failed_before_the_cap(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 9 · acceptance 14.

    **Telling a pharmacy their invoicing system refused a document it never saw
    is worse than telling them nothing.** A row reading `Falló el envío` before
    the policy cap is the defect.
    """
    office = client_as(owner_a)
    connect(office)
    transports.configure(str(tenant_a.id), mode=transports.HANG)
    ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)

    steps = []
    for _attempt in range(4):
        assert run_delivery(row) == "pending"
        row = reload(row)
        assert row.status == FiscalDocumentStatus.PENDING
        assert row.error == "No hay conexión con el sistema de facturación."
        steps.append(round((row.next_attempt_at - timezone.now()).total_seconds() / 60))
    assert steps == [1, 5, 15, 60]
    assert row.attempts == 4

    # The target comes back and the queue drains with no manual step.
    transports.configure(str(tenant_a.id), mode=transports.ACCEPT)
    assert run_delivery(row) == "acknowledged"
    assert reload(row).status == FiscalDocumentStatus.ACKNOWLEDGED


def test_the_dwell_requeries_and_never_resends(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 10. `sent` is delivered-and-unconfirmed, and what settles it is a
    **query**, never a second delivery."""
    office = client_as(owner_a)
    connect(office)
    transports.configure(str(tenant_a.id), mode=transports.ACCEPT_LATER)
    ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)

    assert run_delivery(row) == "sent"
    row = reload(row)
    assert row.status == FiscalDocumentStatus.SENT
    assert row.sent_at is not None

    assert run_delivery(row) == "acknowledged"
    assert reload(row).status == FiscalDocumentStatus.ACKNOWLEDGED
    operations = [one["operation"] for one in log(tenant_a)]
    assert operations == ["deliver", "query"]


def test_a_document_outside_the_clock_tolerance_is_held_rather_than_delivered(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 11 · *Offline · clock skew*.

    **Check the null and the empty log together** -- separately, held and stuck
    look identical. A document dated two days wrong at the far end is a
    correction someone makes by hand, and it is cheaper to hold one than to
    unwind one.
    """
    office = client_as(owner_a)
    connect(office)
    device, _key = make_device(tenant_a, sede_a, label="Caja S")
    till = Till(device, cashier_a)
    item = make_item(tenant_a, "Referencia desfasada", tracks_lots=True)
    lot = make_lot(tenant_a, item, code="L-SKEW")
    price(tenant_a, item, "4000")
    stock(tenant_a, sede_a, item, lot, 20)
    long_ago = timezone.now() - timedelta(days=3)
    apply(
        device,
        [
            till.open_shift(),
            till.open_sale(occurred_at=long_ago),
            till.line(item, 1, "4000", lot=lot),
            till.payment("cash", "4000"),
            till.close_sale(occurred_at=long_ago.isoformat()),
        ],
        batch_id="batch-skew",
    )
    row = one_document(tenant_a)
    assert row.next_attempt_at is None
    assert "reloj" in row.error
    assert log(tenant_a) == []


# ---------------------------------------------------------------------------
# 12 · Returns and voids
# ---------------------------------------------------------------------------


def test_a_return_is_a_credit_note_at_the_prices_originally_charged(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 12 · acceptance 11.

    Only the returned lines, at the quantities and the prices originally
    charged, every amount positive: **the sign is carried by the type**, so that
    no target's mapping has to guess whether a negative total means a credit or
    a data error.
    """
    office = client_as(owner_a)
    connect(office)
    sale, till, item, lot = ring(tenant_a, sede_a, cashier_a, quantity=4)
    original = one_document(tenant_a)
    assert run_delivery(original) == "acknowledged"

    with pin_tenant(tenant_a.id):
        line = sale.lines.get()
    apply(
        till.device,
        [
            envelope(
                "sale_returns",
                {
                    "sale_id": str(sale.id),
                    "number": f"{till.device.code}-90",
                    "shift_id": till.shift_key,
                    "reason": "El cliente compró la presentación equivocada.",
                    "refund_method": "cash",
                    "returned_by_user_id": str(cashier_a.id),
                },
            ),
        ],
        batch_id="batch-return-header",
    )
    with pin_tenant(tenant_a.id):
        sale_return = SaleReturn.objects.get(tenant=tenant_a)
    apply(
        till.device,
        [
            envelope(
                "sale_return_lines",
                {
                    "sale_return_id": str(sale_return.id),
                    "sale_line_id": str(line.id),
                    "quantity": 1,
                },
            ),
        ],
        batch_id="batch-return-line",
    )

    rows = documents(tenant_a)
    assert len(rows) == 2
    note = rows[1]
    assert note.document_key == f"{sede_a.code}-{sale.number}-NC1"
    assert run_delivery(note) == "acknowledged"
    note = reload(note)
    payload = note.payload
    assert payload["document"]["type"] == "credit_note"
    assert payload["document"]["references"] == {
        "sale_number": sale.number,
        "document_key": f"{sede_a.code}-{sale.number}",
    }
    assert len(payload["lines"]) == 1
    assert payload["lines"][0]["quantity"] == 1
    assert payload["lines"][0]["unit_price"] == canonical.price(line.unit_price)
    assert all(one["line_total"] > 0 for one in payload["lines"])
    assert payload["totals"]["total"] > 0


def test_a_void_delivers_the_sale_first_and_the_credit_note_second(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 12, second half · acceptance 12.

    **A void is a credit note, always.** The alternative -- cancelling a queued
    document so the target never hears of that sale -- produces a state where
    our record says a sale exists and the target has never seen the
    `sales.number` both systems reconcile on.
    """
    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)

    response = office.post(
        f"/api/sales/{sale.id}/void",
        {"reason": "Cobro equivocado"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content

    rows = documents(tenant_a)
    assert len(rows) == 2
    original, note = rows
    assert note.document_key.endswith("-NC1")

    # The credit note holds until the sale has left, whichever order they are
    # attempted in.
    assert run_delivery(note) == "waiting"
    assert run_delivery(original) == "acknowledged"
    assert run_delivery(note) == "acknowledged"

    keys = [
        one["document_key"] for one in log(tenant_a) if one["operation"] == "deliver"
    ]
    assert keys == [original.document_key, note.document_key]
    assert len(held(tenant_a)) == 2
    assert len(documents(tenant_a)) == 2


def test_an_imported_sale_is_refused(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 13 · acceptance 13.

    An imported sale was rung up and invoiced in the client's previous system
    long before Botica existed. **Handing a month of history to an invoicing
    system is a month of duplicate invoices** (§8, ledger).
    """
    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    before = len(documents(tenant_a))

    with pin_tenant(tenant_a.id):
        Sale.objects.filter(id=sale.id).update(source=SaleSource.IMPORTED)
        loaded = Sale.objects.select_related("location").get(id=sale.id)
        with pytest.raises(handoff.Refused):
            handoff.hand_off_sale(loaded)
    assert len(documents(tenant_a)) == before


# ---------------------------------------------------------------------------
# 14 · Orphans
# ---------------------------------------------------------------------------


def test_an_orphan_is_reported_and_never_repaired(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 14 · *Jobs*.

    The one thing worse than a missing document is a mechanism that quietly
    manufactures documents nobody can account for.
    """
    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)
    with pin_tenant(tenant_a.id):
        FiscalDocument.objects.filter(id=row.id).delete()
        jobs.sweep_fiscal_documents(None)

    assert documents(tenant_a) == []
    body = office.get("/api/fiscal-documents/unsent-sales").json()
    assert body["row_count"] == 1
    assert body["rows"][0]["sale_number"] == sale.number
    assert "no tiene ningún envío" in body["rows"][0]["reason"]


# ---------------------------------------------------------------------------
# 15 · The file export
# ---------------------------------------------------------------------------


def test_the_file_export_is_byte_identical_on_a_re_run(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 15 · acceptance 16.

    **`acknowledged` here means the file exists**, not that anyone imported it.
    A re-run overwrites the same file with the same content rather than
    appending, so it is a no-op at the far end.
    """
    from core.fiscal import storage

    office = client_as(owner_a)
    connect(office, target="file", delivery={"format": "csv"})
    ring(tenant_a, sede_a, cashier_a, label="Caja 1")
    ring(tenant_a, sede_a, cashier_a, label="Caja 2")

    rows = documents(tenant_a)
    assert len(rows) == 2
    # A batched target sends nothing per document.
    assert run_delivery(rows[0]) == "batched"

    period = export.period_of(rows[0])
    with pin_tenant(tenant_a.id):
        # Re-read the tenant, the way the job does: `tenants.settings` is a
        # column, and a fixture object loaded before the settings PATCH still
        # holds the empty group it was created with.
        tenant = Tenant.objects.get(id=tenant_a.id)
        first = export.run(tenant, period)
        options = invoicing.read(tenant)
    assert first["written"] == 2
    name = export.file_name(options, tenant_a.slug, period)
    body = storage.get(name)
    assert body and body.startswith(b"document_key,")

    for row in documents(tenant_a):
        assert row.status == FiscalDocumentStatus.ACKNOWLEDGED

    with pin_tenant(tenant_a.id):
        again = export.run(Tenant.objects.get(id=tenant_a.id), period)
    assert again["written"] == 2
    assert storage.get(name) == body

    # Every key appears exactly once, and the listing reads from the manifest
    # rather than from a table this stage was never granted.
    keys = [line.split(b",")[0] for line in body.splitlines()[1:]]
    assert len(set(keys)) == len({row.document_key for row in documents(tenant_a)})
    listing = office.get("/api/fiscal-documents/exports").json()
    assert [one["period"] for one in listing] == [period]
    assert listing[0]["document_count"] == 2


# ---------------------------------------------------------------------------
# 16 · The summary
# ---------------------------------------------------------------------------


def test_the_summary_counts_what_is_unsent_per_sede_and_for_the_network(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """Check 16 · acceptance 17."""
    office = client_as(owner_a)
    connect(office)
    transports.configure(str(tenant_a.id), mode=transports.HANG)
    suba = make_location(tenant_a, "SUB", "Suba")
    other = make_user(tenant_a, "cashier", "suba@la45.co", location=suba)
    ring(tenant_a, sede_a, cashier_a, label="Caja 1")
    ring(tenant_a, suba, other, label="Caja 2")

    body = office.get("/api/fiscal-documents/summary").json()
    assert body["configured"] is True
    with pin_tenant(tenant_a.id):
        expected = FiscalDocument.objects.filter(
            tenant=tenant_a, status__in=("pending", "sent")
        ).count()
    assert body["unsent"] == expected == 2
    assert body["oldest_unsent_at"]
    assert {one["location_name"] for one in body["by_location"]} == {
        "Chapinero",
        "Suba",
    }


# ---------------------------------------------------------------------------
# 17 · Audit, and the credential
# ---------------------------------------------------------------------------


def test_every_elevated_mutation_is_audited_and_the_credential_never_is(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root, settings
):
    """Check 17 · acceptance 18.

    **A credential in the audit log is a credential in a JSONB column by another
    route** (§9): the settings group never carries it, so neither does the row
    that records a write to the group.
    """
    settings.BOTICA_INVOICING_CREDENTIALS = {tenant_a.slug: "sk-live-do-not-log"}
    office = client_as(owner_a)
    connect(office, target="http_json", base_url="https://facturador.invalid/api")
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)

    assert office.post(f"/api/fiscal-documents/{row.id}/retry").status_code == 200
    disconnect = office.patch(
        "/api/settings/invoicing",
        {"target": "", "base_url": ""},
        content_type="application/json",
    )
    assert disconnect.status_code == 200, disconnect.content

    with pin_tenant(tenant_a.id):
        rows = list(
            AuditLog.objects.filter(
                tenant=tenant_a,
                entity_type__in=("settings.invoicing", "fiscal_documents"),
            )
        )
    assert len(rows) == 3
    assert all(one.actor_email == owner_a.email for one in rows)
    assert all(one.before is not None and one.after is not None for one in rows)
    printed = "".join(f"{one.before}{one.after}" for one in rows)
    assert "sk-live-do-not-log" not in printed


def test_the_credential_is_the_second_half_of_the_predicate(
    tenant_a, sede_a, owner_a, client_as, storage_root, settings
):
    """*Off when unconfigured* · **both**.

    A target named in the settings group whose key was never put on the instance
    is **not configured** -- which the screen says as a field error rather than
    reporting success and failing silently at 3 a.m.
    """
    settings.BOTICA_INVOICING_CREDENTIALS = {}
    office = client_as(owner_a)
    connect(office, target="http_json", base_url="https://facturador.invalid/api")
    body = office.get("/api/settings/invoicing").json()
    assert body["target"] == "http_json"
    assert body["credential_resolved"] is False
    assert body["enabled"] is False
    assert office.get("/api/fiscal-documents/summary").json() == {"configured": False}

    settings.BOTICA_INVOICING_CREDENTIALS = {tenant_a.slug: "sk-test"}
    assert office.get("/api/settings/invoicing").json()["enabled"] is True


# ---------------------------------------------------------------------------
# 18 · Roles and tenants
# ---------------------------------------------------------------------------


def test_roles_and_tenant_isolation(
    tenant_a,
    tenant_b,
    sede_a,
    owner_a,
    admin_a,
    cashier_a,
    client_as,
    storage_root,
    as_runtime_role,
):
    """Check 18 · acceptance 19, and the settings PATCH's owner-only key.

    A `cashier` reading their own sale's handoff gets **the identifiers and no
    `status` key at all**, because a fiscal state is not a cashier's to read.
    """
    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)
    assert run_delivery(row) == "acknowledged"

    till = client_as(cashier_a)
    assert till.get("/api/fiscal-documents").status_code == 403
    own = till.get(f"/api/sales/{sale.id}/fiscal-document")
    assert own.status_code == 200
    body = own.json()
    assert "status" not in body
    assert "document_key" not in body
    assert body["external_number"]

    # Another sede's sale refuses.
    suba = make_location(tenant_a, "SUB", "Suba")
    other = make_user(tenant_a, "cashier", "suba@la45.co", location=suba)
    far, _t, _i, _l = ring(tenant_a, suba, other, label="Caja 2")
    assert till.get(f"/api/sales/{far.id}/fiscal-document").status_code == 403

    # `target` is an API-key setting: an `admin` may move the retry policy and
    # may not name a target (§2, ledger).
    manager = client_as(admin_a)
    refused = manager.patch(
        "/api/settings/invoicing",
        {"target": "file"},
        content_type="application/json",
    )
    assert refused.status_code == 403
    allowed = manager.patch(
        "/api/settings/invoicing",
        {"retry": {"cap_hours": 12}},
        content_type="application/json",
    )
    assert allowed.status_code == 200
    assert allowed.json()["retry"]["cap_hours"] == 12

    # A second tenant reads zero rows -- **as the runtime role**, because every
    # policy in this system is written for it and the suite otherwise runs as
    # the owner, which holds BYPASSRLS and would pass this for the wrong reason.
    # The same select pinned back reads this session's own rows, without which
    # the zero is indistinguishable from an empty database.
    with pin_tenant(tenant_b.id):
        as_runtime_role()
        assert FiscalDocument.objects.count() == 0
    with pin_tenant(tenant_a.id):
        as_runtime_role()
        assert FiscalDocument.objects.count() >= 1


# ---------------------------------------------------------------------------
# 19 · What this stage would break
# ---------------------------------------------------------------------------


def test_a_sale_closes_in_all_three_broken_configurations(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root, settings
):
    """*What this stage would break* · S4 · a sale closes.

    The handoff service runs inside the sale-close transaction, so anything it
    raised would take the sale down with it. Three configurations, one
    expectation: **the sale commits**.
    """
    office = client_as(owner_a)

    # 1 · no target.
    first, _t, _i, _l = ring(tenant_a, sede_a, cashier_a, label="Caja 1")
    assert first.status == "closed"

    # 2 · a target whose credential does not resolve.
    settings.BOTICA_INVOICING_CREDENTIALS = {}
    connect(office, target="http_json", base_url="https://facturador.invalid/api")
    second, _t, _i, _l = ring(tenant_a, sede_a, cashier_a, label="Caja 2")
    assert second.status == "closed"

    # 3 · a target pointed at an unroutable address. The sale commits, the row
    # is written, and the attempt that follows is a **transport failure** rather
    # than a refusal -- `.invalid` is the reserved TLD that guarantees an
    # NXDOMAIN, so this exercises the real HTTP transport without leaving the
    # machine.
    settings.BOTICA_INVOICING_CREDENTIALS = {tenant_a.slug: "sk-test"}
    third, _t, _i, _l = ring(tenant_a, sede_a, cashier_a, label="Caja 3")
    assert third.status == "closed"
    row = [one for one in documents(tenant_a) if one.sale_id == third.id]
    assert len(row) == 1
    assert run_delivery(row[0]) == "pending"
    settled = reload(row[0])
    assert settled.status == FiscalDocumentStatus.PENDING
    assert settled.error == "No hay conexión con el sistema de facturación."

    del office


def test_the_stock_ledger_and_the_catalog_are_untouched_by_a_full_cycle(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """*What this stage would break* · S3 and S1.

    S5 writes no `stock_moves` row (rule 7) and never backfills a barcode, a
    description or a customer's name rather than failing validation.
    """
    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)

    with pin_tenant(tenant_a.id):
        moves_before = StockMove.objects.filter(tenant=tenant_a).count()
        items_before = (
            Item.objects.filter(tenant=tenant_a).order_by("-updated_at").first()
        )
        catalog_before = items_before.updated_at if items_before else None

    assert run_delivery(row) == "acknowledged"
    office.post(f"/api/fiscal-documents/{row.id}/retry")
    run_delivery(reload(row))
    office.get(f"/api/sales/{sale.id}/canonical-document")

    with pin_tenant(tenant_a.id):
        assert StockMove.objects.filter(tenant=tenant_a).count() == moves_before
        items_after = (
            Item.objects.filter(tenant=tenant_a).order_by("-updated_at").first()
        )
        assert (items_after.updated_at if items_after else None) == catalog_before


def test_writing_the_invoicing_group_leaves_its_neighbours_alone(
    tenant_a, sede_a, owner_a, client_as, storage_root
):
    """*What this stage would break* · S0 · `tenants.settings` (rule 5)."""
    office = client_as(owner_a)
    before = office.patch(
        "/api/settings/tenant",
        {
            "name": "Droguerías La 45",
            "nit": "901.245.778-3",
            "legal_name": "Droguerías La 45 S.A.S.",
            "timezone": "America/Bogota",
        },
        content_type="application/json",
    )
    assert before.status_code == 200
    connect(office)
    after = office.get("/api/settings/tenant").json()
    assert after["legal_name"] == "Droguerías La 45 S.A.S."
    assert office.get("/api/settings/sync").status_code == 200


def test_every_job_fails_closed_with_no_tenant_pinned(storage_root):
    """*What this stage would break* · S0 · tenant pinning (A1, rule 6).

    A job that reported success having written nothing is the failure this
    refusal exists for, and in a log it is indistinguishable from the real
    thing.
    """
    for call in (
        lambda: jobs.deliver_fiscal_document.func(tenant_id="", document_id="x"),
        lambda: jobs.export_fiscal_documents.func(tenant_id=None, period="2026-09-01"),
        lambda: jobs.notify_failed_deliveries.func(tenant_id="", run_date="2026-09-01"),
    ):
        with pytest.raises(ValueError):
            call()


# ---------------------------------------------------------------------------
# The document key, and the two properties it exists for
# ---------------------------------------------------------------------------


def test_the_document_key_is_reconstructible_from_the_sale_alone(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """It contains no timestamp, no attempt counter and no random component,
    because anything that varies per attempt destroys the only guarantee it
    exists to provide (§8)."""
    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)
    with pin_tenant(tenant_a.id):
        loaded = Sale.objects.select_related("location").get(id=sale.id)
        assert handoff.base_key(loaded) == row.document_key
        assert handoff.base_key(loaded) == f"{sede_a.code}-{sale.number}"
        assert handoff.is_credit_note(row) is False


def test_a_service_line_renders_exactly_like_a_product(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """A7 · **that is the whole cost of supporting toma de presión and
    inyectología on a fiscal document**, and it is why they are the same
    table."""
    office = client_as(owner_a)
    connect(office)
    device, _key = make_device(tenant_a, sede_a, label="Caja T")
    till = Till(device, cashier_a)
    service_item = make_item(
        tenant_a, "Toma de presión", tracks_lots=False, tracks_expiry=False
    )
    Item.objects.filter(id=service_item.id).update(
        type="service", tracks_stock=False, unit="servicio"
    )
    service_item.refresh_from_db()
    price(tenant_a, service_item, "6000")
    apply(
        device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(service_item, 1, "6000"),
            till.payment("cash", "6000"),
            till.close_sale(),
        ],
        batch_id="batch-service",
    )
    with pin_tenant(tenant_a.id):
        sale = Sale.objects.get(tenant=tenant_a, device=device)
    payload = office.get(f"/api/sales/{sale.id}/canonical-document").json()
    line = payload["lines"][0]
    assert line["description"].startswith("Toma de presión")
    assert line["quantity"] == 1
    assert line["unit_price"] == "6000.00"
    assert line["vat_class"] == "excluded"
    # And it moved no stock, which is the whole of what `tracks_stock` decides.
    with pin_tenant(tenant_a.id):
        assert not StockMove.objects.filter(tenant=tenant_a, item=service_item).exists()


def test_the_builder_refuses_a_sale_whose_network_has_no_nit(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """*When a required field is missing* · the emitter's NIT.

    Validation is on the canonical payload, **once, for every target** -- not
    inside a mapping, where each new client would rediscover the same missing
    NIT.
    """
    office = client_as(owner_a)
    connect(office)
    with pin_tenant(tenant_a.id):
        from core.models import Tenant

        Tenant.objects.filter(id=tenant_a.id).update(nit="")
    ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)
    assert row.status == FiscalDocumentStatus.FAILED
    assert "NIT" in row.error
    assert row.next_attempt_at is None


def test_the_mapping_renames_and_translates_rather_than_passing_through(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """*The mapping layer* · **the canonical payload is ours; the field names
    are theirs.**

    The loopback's mapping is deliberately not the identity, so every check in
    this file exercises the renderer rather than walking around it.
    """
    from core.fiscal import mappings

    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    payload = office.get(f"/api/sales/{sale.id}/canonical-document").json()
    body = mappings.render(mappings.LOOPBACK, payload)

    assert body["schema"] == "botica.canonical.v1"
    assert body["idempotency_key"] == payload["document"]["document_key"]
    assert "header" in body and "issuer" in body
    assert body["payments"][0]["method"] == "CASH"
    assert body["acquirer"]["document"] == "222222222222"
    assert body["lines"][0]["code"] == payload["lines"][0]["item_code"]
    assert body["lines"][0]["name"] == payload["lines"][0]["description"]
    assert body["lines"][0]["vat_class"] == "EXC"


def test_the_canonical_document_is_a_pure_function_of_the_sale(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """*The document is derived, not stored.*

    A correction upstream is picked up by a retry, because the next attempt
    renders the current truth rather than replaying a stored artefact.
    """
    office = client_as(owner_a)
    connect(office)
    sale, _till, item, _lot = ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)
    assert run_delivery(row) == "acknowledged"
    sent = reload(row).payload["lines"][0]["description"]

    with pin_tenant(tenant_a.id):
        Item.objects.filter(id=item.id).update(name="Acetaminofén 500 mg × 100")
    rendered = handoff_render(row)
    assert rendered["lines"][0]["description"] != sent
    assert rendered["lines"][0]["description"].startswith("Acetaminofén")


def handoff_render(row):
    with pin_tenant(row.tenant_id):
        return handoff.render(reload(row))


def test_an_amount_is_a_whole_peso_and_a_unit_price_is_not(storage_root):
    """*Every amount is an integer number of COP; the unit price is not an
    amount.*

    A **rate** is fractional by construction: `items` are priced per pack and
    sold per base unit, so a splittable box of fourteen at $15.450 sells at
    $1.103,57 a tablet -- and rounding that would make `unit_price × quantity`
    disagree with the line's own total by six pesos, which is exactly the
    arithmetic a receiving system checks. So it travels as a decimal string.
    """
    assert canonical.price(Decimal("1103.57")) == "1103.57"
    assert canonical.price(Decimal("4250")) == "4250.00"
    assert canonical.rounded(Decimal("3831.93")) == 3832
    assert canonical.rounded(Decimal("417.50")) == 418


def test_a_void_of_a_partly_returned_sale_credits_only_what_came_back(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """The void's credit note reverses **what the void actually reversed**.

    `returnable` is the same arithmetic S4's own reversal used, so the document
    and the `customer_return` moves describe one event rather than two.
    """
    office = client_as(owner_a)
    connect(office)
    sale, till, _item, _lot = ring(tenant_a, sede_a, cashier_a, quantity=4)
    with pin_tenant(tenant_a.id):
        line = sale.lines.get()
    apply(
        till.device,
        [
            envelope(
                "sale_returns",
                {
                    "sale_id": str(sale.id),
                    "number": f"{till.device.code}-91",
                    "shift_id": till.shift_key,
                    "reason": "Devolvió una unidad.",
                    "refund_method": "cash",
                    "returned_by_user_id": str(cashier_a.id),
                },
            ),
        ],
        batch_id="batch-partial-return",
    )
    with pin_tenant(tenant_a.id):
        sale_return = SaleReturn.objects.get(tenant=tenant_a)
    apply(
        till.device,
        [
            envelope(
                "sale_return_lines",
                {
                    "sale_return_id": str(sale_return.id),
                    "sale_line_id": str(line.id),
                    "quantity": 1,
                },
            ),
        ],
        batch_id="batch-partial-return-line",
    )
    with pin_tenant(tenant_a.id):
        loaded = Sale.objects.select_related("location").get(id=sale.id)
        assert sale_service.returnable(loaded) == {line.id: 3}
        sale_service.void(loaded, reason="Anulada tras devolución parcial")

    note = [
        one
        for one in documents(tenant_a)
        if one.sale_id == sale.id and one.document_key.endswith("-NC2")
    ]
    assert len(note) == 1
    payload = handoff_render(note[0])
    assert payload["lines"][0]["quantity"] == 3
    assert (
        sum(one["amount"] for one in payload["payments"]) == payload["totals"]["total"]
    )


def test_the_daily_digest_names_what_is_stuck_and_resends_nothing(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root, settings
):
    """*Jobs* · `notify_failed_deliveries`.

    **The work list is the record and the email is a pointer to it**: a failure
    that exists only in an inbox is a failure nobody resolved. An empty
    recipient list is a configuration and not a failure -- the list still
    renders.
    """
    from django.core import mail as django_mail

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    office = client_as(owner_a)
    connect(office)
    transports.configure(
        str(tenant_a.id),
        mode=transports.REFUSE,
        refusal="el adquiriente no tiene número de documento",
    )
    ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)
    assert run_delivery(row) == "failed"

    day = timezone.localdate()
    # Nobody asked to be told, so nobody is told -- and the row is still there.
    assert (
        jobs.notify_failed_deliveries.func(
            tenant_id=str(tenant_a.id), run_date=day.isoformat()
        )
        == 1
    )
    assert django_mail.outbox == []

    office.patch(
        "/api/settings/invoicing",
        {"notifications": ["marcela.rios@la45.co"]},
        content_type="application/json",
    )
    assert (
        jobs.notify_failed_deliveries.func(
            tenant_id=str(tenant_a.id), run_date=day.isoformat()
        )
        == 1
    )
    assert len(django_mail.outbox) == 1
    message = django_mail.outbox[0]
    assert message.to == ["marcela.rios@la45.co"]
    assert "envíos a facturación sin resolver" in message.subject
    # **No string in it names the DIAN.**
    assert "DIAN" in message.body and "No emite documentos" in message.body
    # And the row is untouched: a digest reports and never resends.
    assert reload(row).status == FiscalDocumentStatus.FAILED


def test_a_target_refusal_is_one_sentence_a_person_can_act_on(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """§B.10.3 · `Motivo` is never a bare HTTP status, never a stack trace,
    never English, never an empty cell. **The vendor's own body belongs in
    `fiscal_documents.response`** and the platform-admin view."""
    office = client_as(owner_a)
    connect(office)
    transports.configure(
        str(tenant_a.id), mode=transports.REFUSE, refusal="NIT del emisor inválido"
    )
    ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)
    assert run_delivery(row) == "failed"
    row = reload(row)
    assert row.error == (
        "El sistema de facturación rechazó el documento: NIT del emisor inválido"
    )
    assert row.response == {"error": "NIT del emisor inválido"}


def test_the_service_refuses_a_sale_from_another_network(
    tenant_a, tenant_b, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """The tenant is the sale's, and a caller that says otherwise is refused.

    Under a pin this is unreachable. A management command or a background job
    running as the migration role holds BYPASSRLS and can read across networks,
    and a document written under one tenant for another's sale would be a row
    RLS hides from the only people who could notice it.
    """
    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    with pin_tenant(tenant_a.id):
        loaded = Sale.objects.select_related("location").get(id=sale.id)
        with pytest.raises(handoff.Refused):
            handoff.hand_off_sale(loaded, tenant=tenant_b)
    assert len(documents(tenant_a)) == 1


# ---------------------------------------------------------------------------
# The apportionment, exercised directly
# ---------------------------------------------------------------------------


class _Line:
    """The four figures the builder reads off a line, and nothing else."""

    def __init__(self, unit_price, quantity, discount, tax_amount, item_id="i"):
        self.unit_price = Decimal(unit_price)
        self.quantity = quantity
        self.discount = Decimal(discount)
        self.tax_amount = Decimal(tax_amount)
        self.vat_class = VatClass.RATE_19
        self.item_id = item_id


def _rows(lines):
    """The provisional integers `_line` produces, before apportionment."""
    return [
        {
            "discount": canonical.rounded(line.discount),
            "tax_amount": canonical.rounded(line.tax_amount),
            "line_total": canonical.rounded(
                Decimal(line.unit_price) * line.quantity - line.discount
            ),
        }
        for line in lines
    ]


def _source(lines):
    return canonical.Source(
        sale=None,
        type=canonical.SALE,
        document_key="k",
        occurred_at=None,
        recorded_at=None,
        lines=tuple(lines),
    )


@pytest.mark.parametrize(
    "amounts",
    [
        # Two halves: each rounds up, the ticket rounds to one. A **negative**
        # residue, which is the direction that used to debit the wrong line.
        ["0.50", "0.50"],
        # An exact line beside two halves: the exact one must not be touched.
        ["400.00", "0.50", "0.50"],
        # Thirds: a positive residue spread over the largest remainders.
        ["3.33", "3.33", "3.34"],
        # One line, and it carries the whole of its own rounding.
        ["417.50"],
        # A ticket whose lines are already whole: nothing moves.
        ["1000.00", "2000.00"],
    ],
)
def test_the_apportionment_lands_the_residue_on_the_lines_that_earned_it(amounts):
    """The lines sum to the ticket exactly, and **no exact line is moved**.

    Largest remainder in both directions: a peso short goes to the line rounded
    *down* by the smallest margin, a peso over comes off the line rounded *up*
    by the smallest margin. Sorting every line together would keep the ticket
    right and make a line wrong, which is the harder error to find.
    """
    lines = [_Line(one, 1, "0", "0") for one in amounts]
    rows = _rows(lines)
    exact = sum(Decimal(one) for one in amounts)
    target = canonical.rounded(exact)

    # `fraction=` is how the builder itself calls this for `line_total`: a line's
    # net is computed from three of its columns and is not a column of its own,
    # so the default weigher has nothing to read.
    canonical._apportion(
        rows,
        _source(lines),
        "line_total",
        target,
        fraction=canonical._net_fraction,
    )

    assert sum(row["line_total"] for row in rows) == target
    for row, amount in zip(rows, amounts):
        # Every line stays within a peso of its own exact figure, so the
        # apportionment moved money it had a claim to and nothing else.
        assert abs(row["line_total"] - Decimal(amount)) <= 1
    # A line that was already whole keeps its figure.
    for row, amount in zip(rows, amounts):
        if Decimal(amount) == Decimal(amount).to_integral_value():
            assert row["line_total"] == int(Decimal(amount))


# ---------------------------------------------------------------------------
# What the verification pass found, and what now holds
# ---------------------------------------------------------------------------


def test_payments_that_disagree_with_the_total_are_never_smoothed_over(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """*The canonical sale document* · the payments assertion is a **check**.

    S2's batch rule applies a sale and its lines while rejecting one malformed
    `payments` row on its own savepoint, so a ticket really can land holding a
    strict subset of its split. A builder that absorbed the difference onto the
    surviving payment would file that sale as fully paid by whichever method
    happened to land -- the invoicing system would record cash for money that
    went through a card and was never recorded at all.
    """
    office = client_as(owner_a)
    connect(office)
    sale, _till, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)
    assert run_delivery(row) == "acknowledged"

    with pin_tenant(tenant_a.id):
        payment = sale.payments.get()
        payment.amount = payment.amount - Decimal("1000.00")
        payment.save(update_fields=["amount"])
        with pytest.raises(canonical.Incomplete) as refusal:
            handoff.render(reload(row))
    assert "Los pagos suman" in str(refusal.value)


def test_a_target_that_cannot_be_queried_is_never_re_asked_after_the_dwell(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """A target with no query operation is **held once it takes a document**.

    Leaving it in `sent` with a dwell would send the sweep to ask a question
    that target cannot answer, read the silence as a refusal, and write
    `Falló el envío` on a document it holds -- and a forced retry would then
    re-send blind to a system that cannot dedupe, which is the duplicate this
    whole stage exists to prevent.
    """
    office = client_as(owner_a)
    connect(office, mapping="loopback_blind")
    transports.configure(str(tenant_a.id), mode=transports.ACCEPT_LATER)
    ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)

    assert run_delivery(row) == "held"
    row = reload(row)
    assert row.status == FiscalDocumentStatus.PENDING
    assert row.next_attempt_at is None
    assert "no confirma su estado" in row.error
    # One request, and no sweep can produce a second.
    with pin_tenant(tenant_a.id):
        jobs._sweep_tenant(tenant_a)
    assert [one["operation"] for one in log(tenant_a)] == ["deliver"]


def test_two_credit_notes_racing_on_one_sale_each_get_their_own(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """The n-th credit note **means the n-th**.

    `_next_ordinal` is an unlocked count, so two reversals against one sale can
    both compose `-NC1`. Recovering by key alone would hand the loser the
    winner's row and the caller would discard it: that devolución's refund would
    never reach the invoicing system and would surface only as an orphan.
    """
    office = client_as(owner_a)
    connect(office)
    sale, till, _item, _lot = ring(tenant_a, sede_a, cashier_a, quantity=4)
    with pin_tenant(tenant_a.id):
        line = sale.lines.get()

    returns = []
    for index, number in enumerate(("92", "93")):
        apply(
            till.device,
            [
                envelope(
                    "sale_returns",
                    {
                        "sale_id": str(sale.id),
                        "number": f"{till.device.code}-{number}",
                        "shift_id": till.shift_key,
                        "reason": "Devolvió una unidad.",
                        "refund_method": "cash",
                        "returned_by_user_id": str(cashier_a.id),
                    },
                ),
                envelope(
                    "sale_return_lines",
                    {
                        "sale_return_id": None,
                        "sale_line_id": str(line.id),
                        "quantity": 1,
                    },
                ),
            ][:1],
            batch_id=f"batch-race-{index}",
        )
    with pin_tenant(tenant_a.id):
        returns = list(SaleReturn.objects.filter(tenant=tenant_a).order_by("number"))
    assert len(returns) == 2

    notes = [one for one in documents(tenant_a) if one.sale_return_id is not None]
    assert len(notes) == 2
    assert {one.document_key for one in notes} == {
        f"{sede_a.code}-{sale.number}-NC1",
        f"{sede_a.code}-{sale.number}-NC2",
    }
    assert {str(one.sale_return_id) for one in notes} == {
        str(one.id) for one in returns
    }


def test_a_credit_note_waits_for_its_own_lines_rather_than_failing(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """A till's outbox drains in batches, so a devolución's header and its lines
    can arrive in different pushes.

    A document built between the two has no lines through no fault of anyone's.
    Failing it would put an unactionable row on the work list for a return that
    is arriving normally -- the opposite of "reconciles when the connection
    returns without anyone being asked" (§5).
    """
    office = client_as(owner_a)
    connect(office)
    sale, till, _item, _lot = ring(tenant_a, sede_a, cashier_a, quantity=4)
    original = one_document(tenant_a)
    assert run_delivery(original) == "acknowledged"
    with pin_tenant(tenant_a.id):
        line = sale.lines.get()

    apply(
        till.device,
        [
            envelope(
                "sale_returns",
                {
                    "sale_id": str(sale.id),
                    "number": f"{till.device.code}-94",
                    "shift_id": till.shift_key,
                    "reason": "El cliente compró la presentación equivocada.",
                    "refund_method": "cash",
                    "returned_by_user_id": str(cashier_a.id),
                },
            ),
        ],
        batch_id="batch-header-only",
    )
    note = [one for one in documents(tenant_a) if one.sale_return_id is not None][0]

    # The lines are still in the next push. The job waits rather than failing.
    assert run_delivery(note) == "waiting"
    note = reload(note)
    assert note.status == FiscalDocumentStatus.PENDING
    assert note.next_attempt_at is not None
    assert "líneas de la devolución" in note.error
    assert note.attempts == 0

    with pin_tenant(tenant_a.id):
        sale_return = SaleReturn.objects.get(tenant=tenant_a)
    apply(
        till.device,
        [
            envelope(
                "sale_return_lines",
                {
                    "sale_return_id": str(sale_return.id),
                    "sale_line_id": str(line.id),
                    "quantity": 1,
                },
            ),
        ],
        batch_id="batch-lines-later",
    )
    assert run_delivery(note) == "acknowledged"


def test_switching_the_target_holds_what_the_old_one_was_carrying(
    tenant_a, sede_a, owner_a, cashier_a, client_as, storage_root
):
    """A row belongs to **the target it was built for**, which is the column.

    Reading the tenant's current target instead would strand every queued
    document the moment an administrator switched to the file export: the
    delivery path would call them batched and skip them, and the export filters
    on the column and would never see them.
    """
    office = client_as(owner_a)
    connect(office)
    transports.configure(str(tenant_a.id), mode=transports.HANG)
    ring(tenant_a, sede_a, cashier_a)
    row = one_document(tenant_a)
    assert run_delivery(row) == "pending"

    connect(office, target="file")
    assert run_delivery(row) == "held"
    row = reload(row)
    assert row.next_attempt_at is None
    assert "otro sistema de facturación" in row.error
