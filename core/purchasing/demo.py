"""The `purchasing` fixture, registered with **S0's** `seed_demo_tenant`.

S6 ships no seed command. It registers one fixture, declares S4's sales as its
dependency, and **writes no forecast row and no order line by hand**: it runs
`forecast.refresh` and `purchase_order.generate` exactly as the cron does, then
walks the resulting orders through the product's own service functions.

*That is the whole design of this file.* A regime that appeared only because a
fixture set `basis` is a regime nobody has tested, and the drawn numbers are
worth less than the guarantee that the screen shows what the code produces. So
this fixture tunes nothing about the model: it takes what the sales S4 wrote
produce, and its only editorial acts are downstream of the arithmetic -- which
order is approved, which is sent, which is received short.

**No gateway call.** `reason` prose for the seed's `learned` lines comes from a
fixed table, so pantalla 4 renders without a paid model call on every seed run;
`parametric` and `learning` lines carry the deterministic strings they would
carry in production anyway. **The table supplies prose only for codes the seed's
own window can produce**: the two seasonal strings the handoff draws resolve from
a year-ago category multiplier, which needs 52 weeks, and the seed carries 180
days. Those rows render the reason their computed code gives, and the drawing
yields to the true number -- exactly as the provenance line does.

**No imported sales.** The seeded tenant's history is Botica's own, because a
demo that only works after an import demonstrates exactly the precondition §1
says must not exist. The loader is exercised by its own fixture file in the test
suite instead.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from core.demo.registry import register
from core.inventory import settings as inventory_settings, states
from core.models import (
    ForecastBasis,
    GoodsReceiptLine,
    Item,
    Location,
    PolicySource,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderSource,
    PurchaseOrderStatus,
    StockOnHand,
    StockPolicy,
    SupplierItem,
    Tenant,
    User,
)
from core.purchasing import forecast, orders as order_service, receiving

logger = logging.getLogger(__name__)

#: The sede and the supplier the handoff draws, and the pair every check in this
#: stage opens first.
DRAWN_SEDE = "CHA"
DRAWN_SUPPLIER = "Coopidrogas"

#: How long before the seed date the two downstream orders were approved and
#: sent, so `suppliers.lead_time_days` has a real order-to-receipt interval to be
#: the median of rather than a number somebody typed.
SENT_DAYS_BACK = 5
RECEIVED_DAYS_BACK = 2

#: What the partial receipt does to the order it is taken against: the first
#: line arrives short, the second whole, the third over. All three states the
#: receiving screen has to render, on one document, without anyone typing.
SHORT_SHARE = Decimal("0.9")
OVER_UNITS = 20

#: The two typed orders the seed receives against. Three lines each, because
#: three is what it takes to show a short line, a whole one and an over-delivery
#: on one document.
MANUAL_ORDER_LINES = 3
MANUAL_ORDER_UNITS = 24

#: The prose the drawn order's `learned` lines carry, by reason code. **Only
#: codes this seed's own 180-day window can produce are in it** -- there is no
#: seasonal entry, because a seed that canned a pollen season out of twenty-six
#: weeks so the pixels matched the handoff would be the same failure this stage
#: already refuses for `18 meses`.
PROSE = {
    "stable_rotation": "Rotación estable en las 6 sedes",
    "predictable_chronic": "Crónico, demanda predecible",
    "cross_sell_pair": "Se vende junto con suero oral",
    "stockout_available_elsewhere": "Sustituto en quiebre, reponer esta semana",
    "lot_expiring": "El lote en góndola vence antes de rotar",
    "overstock": "Sobrestock, liberar capital",
    "sufficient_coverage": "Cobertura suficiente, no pedir",
}

#: Which profiles build anything at all, and what.
#:
#: `cold` and `minimal` are the two the *Verification* section reaches for by
#: name and neither is reached by editing rows: `cold` runs the same two jobs
#: over a tenant with catalog and stock and no sales, which is how the
#: parametric regime is reached; `minimal` registers nothing, because it exists
#: to be the tenant this stage's command and jobs must never write into.
PROFILES = {
    "default": {"orders": True, "downstream": True},
    "young": {"orders": True, "downstream": False},
    "cold": {"orders": True, "downstream": False},
    "scale": {"orders": True, "downstream": False},
    "minimal": {"orders": False, "downstream": False},
}


def build(context):
    """Run the two jobs, then walk the orders they produced."""
    shape = PROFILES[context.profile]
    if not shape["orders"]:
        context.wrote("purchase_orders", 0)
        context.note("  compras         sin órdenes (perfil sin ventas ni catálogo)")
        return

    today = timezone.localdate()
    locations = list(
        Location.objects.filter(tenant_id=context.tenant_id).order_by("code")
    )
    forecasts = 0
    for location in locations:
        report = forecast.refresh(context.tenant_id, location.id, today=today)
        forecasts += report.get("written", 0)
    context.wrote("demand_forecasts", forecasts)

    built = []
    for location in locations:
        built.extend(
            order_service.generate(context.tenant_id, location.id, today=today)
        )
    context.wrote("purchase_orders", len(built))
    context.wrote(
        "purchase_order_lines",
        PurchaseOrderLine.objects.filter(tenant_id=context.tenant_id).count(),
    )
    context.wrote(
        "stock_policies",
        StockPolicy.objects.filter(
            tenant_id=context.tenant_id, source=PolicySource.MODEL
        ).count(),
    )

    _write_prose(context)
    if shape["downstream"]:
        _walk_downstream(context, built, today)

    drawn = _drawn_order(context)
    if drawn is not None:
        suggested = PurchaseOrderLine.objects.filter(
            purchase_order=drawn, approved_quantity__gt=0
        ).count()
        context.note(
            f"  compras         orden {drawn.number} en {DRAWN_SUPPLIER} · "
            f"{suggested} referencias sugeridas"
        )


def _drawn_order(context):
    return (
        PurchaseOrder.objects.filter(
            tenant_id=context.tenant_id,
            location__code=DRAWN_SEDE,
            supplier__name=DRAWN_SUPPLIER,
            status=PurchaseOrderStatus.SUGGESTED,
        )
        .order_by("number")
        .first()
    )


def _write_prose(context):
    """The model's clause on every `learned` line, from the fixed table.

    Written directly rather than through `purchase_order.reason_text`, because
    that job's only job is to call a paid vendor -- and a seed that made a
    network call on every run would be a seed nobody could build on a plane.
    """
    written = 0
    for code, clause in PROSE.items():
        written += PurchaseOrderLine.objects.filter(
            tenant_id=context.tenant_id,
            basis=ForecastBasis.LEARNED,
            reason_code=code,
        ).update(reason=clause)
    logger.info("seeded reason prose on %s line(s)", written)


def _walk_downstream(context, built, today):
    """One order at each status, and two of them received.

    All six `purchase_order_status` badges are then visible on one page of
    Órdenes de compra, `Recepción` is reachable from a real `sent` order, and a
    supplier's `lead_time_days` and a `supplier_items.cost` are observed from a
    confirmed receipt rather than typed.

    **The two orders that get received are written by hand, and that is not a
    convenience.** A model order proposes exactly the references that need
    ordering -- the ones at or below their reorder point -- and receiving those
    would move rows out of the action states S3's own fixture counts exactly.
    S4's fixture takes the same care from the other side, selling only from
    `sufficient` and `overstock` rows. A manual order is also the honest shape
    for the seed's one received delivery: `source = manual` exists so the enum
    value is not dead, the `Origen` chip has something to filter, and a pilot's
    first receipt is usually an order somebody typed.
    """
    if PurchaseOrder.objects.filter(
        tenant_id=context.tenant_id, source=PurchaseOrderSource.MANUAL
    ).exists():
        # The walk has already run on this tenant. Running it again would
        # approve and discard a second morning's orders every rebuild.
        logger.info("the downstream walk has already run on this tenant")
        return

    drawn = _drawn_order(context)
    spare = [
        order
        for order in sorted(built, key=lambda one: (str(one.location_id), one.number))
        if drawn is None or order.id != drawn.id
    ]
    if len(spare) < 3:
        # A profile too small to show every badge shows the ones it can rather
        # than inventing an order nothing produced.
        logger.info("only %s spare order(s); skipping the downstream walk", len(spare))
        return

    admin = (
        User.objects.filter(tenant_id=context.tenant_id, role="admin")
        .order_by("email")
        .first()
    )
    approved, sent, discarded = spare[0], spare[1], spare[2]

    order_service.approve(approved, actor=admin)
    order_service.approve(sent, actor=admin)
    order_service.mark_sent(sent, at=timezone.now() - timedelta(days=SENT_DAYS_BACK))
    order_service.discard(discarded)

    for ordinal, partial in ((0, True), (1, False)):
        order = _manual_order(context, admin, ordinal)
        if order is None:
            continue
        order_service.approve(order, actor=admin)
        order_service.mark_sent(
            order, at=timezone.now() - timedelta(days=SENT_DAYS_BACK + ordinal)
        )
        _receive(context, order, today, partial=partial)


def _manual_order(context, admin, ordinal):
    """One typed order, on references whose shelves can take the delivery.

    Written through the same tables the endpoint writes, with
    `suggested_quantity` **null** on every line: nobody proposed anything, and a
    zero there would enter the deviation measurement as a proposal of nothing.
    """
    location = Location.objects.filter(
        tenant_id=context.tenant_id, code=DRAWN_SEDE
    ).first()
    if location is None:
        return None

    comfortable = _comfortable_items(context, location.id)
    if not comfortable:
        logger.info("no comfortable shelf at %s to write a manual order on", DRAWN_SEDE)
        return None

    links = (
        SupplierItem.objects.filter(
            tenant_id=context.tenant_id,
            item_id__in=list(comfortable),
            cost__isnull=False,
            is_preferred=True,
        )
        .select_related("item")
        .order_by("supplier__name", "item__name")
    )
    by_supplier: dict = {}
    for link in links:
        by_supplier.setdefault(link.supplier_id, []).append(link)
    chosen = [
        (supplier_id, rows)
        for supplier_id, rows in sorted(
            by_supplier.items(), key=lambda one: str(one[0])
        )
        if len(rows) >= MANUAL_ORDER_LINES
    ]
    if len(chosen) <= ordinal:
        return None
    supplier_id, rows = chosen[ordinal]

    order_service.lock_location(context.tenant_id, location.id)
    order = PurchaseOrder.objects.create(
        tenant_id=context.tenant_id,
        location=location,
        supplier_id=supplier_id,
        number=order_service.next_number(context.tenant_id, location.id),
        status=PurchaseOrderStatus.SUGGESTED,
        source=PurchaseOrderSource.MANUAL,
        approved_by=admin,
        approved_by_name=admin.name if admin else "",
    )
    PurchaseOrderLine.objects.bulk_create(
        [
            PurchaseOrderLine(
                tenant_id=context.tenant_id,
                purchase_order=order,
                item=link.item,
                suggested_quantity=None,
                approved_quantity=MANUAL_ORDER_UNITS + index,
                unit_cost=(
                    link.cost / Decimal(max(1, link.item.units_per_pack))
                ).quantize(Decimal("0.01")),
            )
            for index, link in enumerate(rows[:MANUAL_ORDER_LINES])
        ]
    )
    order_service.recompute_total(order)
    context.wrote("purchase_orders", 1)
    context.wrote("purchase_order_lines", MANUAL_ORDER_LINES)
    return order


def _receive(context, order, today, *, partial):
    """Open a receipt against one `sent` order, type it, and confirm it.

    Through `receiving.confirm`, which is the product's own path: the lots are
    attached, S3's ledger service writes one `receipt` move per line,
    `lots.unit_cost` and `supplier_items.cost` are written from what was paid,
    and the order settles at `Recibida parcial` or `Recibida`.

    **It receives only into shelves S3's own fixture planned as comfortable, and
    only into lots those shelves already hold.** S4's fixture takes the same
    care from the other side -- it sells only from `sufficient` and `overstock`
    rows -- and for the same reason: Existencias is the most data-dense surface
    in the product and S3 tunes it to an exact grid, so a stage that received
    merchandise wherever it liked would move rows across state boundaries and
    leave S3's screen looking like a bug in someone else's stage. Attaching to
    a lot the sede already holds is also the ordinary case in a droguería, and
    it is the path `resolve_lot` takes on a second delivery of one lot.
    """
    receipt = receiving.open_against(
        order,
        received_at=timezone.now() - timedelta(days=RECEIVED_DAYS_BACK),
        supplier_document_number=f"REM-{order.number:05d}",
    )
    lines = list(receipt.lines.select_related("item").order_by("item__name"))
    holdings = _comfortable_lots(
        context, order.location_id, [line.item_id for line in lines]
    )
    lines = [line for line in lines if line.item_id in holdings]
    GoodsReceiptLine.objects.filter(goods_receipt=receipt).exclude(
        id__in=[line.id for line in lines]
    ).delete()
    if not lines:
        logger.info("no comfortable shelf to receive order %s into", order.number)
        return

    for position, line in enumerate(lines):
        quantity = line.quantity
        if partial and position == 0:
            quantity = max(1, int(Decimal(quantity) * SHORT_SHARE))
        elif partial and position == 2:
            quantity = quantity + OVER_UNITS
        code, expires_at = holdings[line.item_id]
        line.quantity = quantity
        line.lot_code = code
        line.expires_at = expires_at
        line.save(update_fields=["quantity", "lot_code", "expires_at", "updated_at"])

    receiving.confirm(
        receipt,
        move_id_for=lambda line: context.uid(
            "stock_moves", f"receipt:{receipt.id}:{line.id}"
        ),
    )
    context.wrote("goods_receipts", 1)
    context.wrote("goods_receipt_lines", len(lines))
    _refresh_lead_time(context, order)


def _refresh_lead_time(context, order):
    receiving.refresh_lead_time(context.tenant_id, order.supplier_id)


def _comfortable_lots(context, location_id, item_ids):
    """`item_id -> (lot code, expiry)` for the references a receipt may take.

    A reference is admitted only where **every** shelf row for it at that sede
    is `sufficient` or `overstock` -- so the units arriving cannot move a row
    out of an action state and change the count S3's own fixture is tuned to.
    Receiving only ever adds units, so it can move a row *into* `overstock`,
    which is not an action state, and never into one.

    The lot returned is one the sede already holds, so no new `stock_on_hand`
    key appears either and the grid keeps its row count -- and attaching to a
    standing lot is the path `resolve_lot` takes on a second delivery of one
    lot, which is the ordinary case in a droguería.
    """
    tenant = Tenant.objects.get(id=context.tenant_id)
    options = inventory_settings.read(tenant)
    rows = states.annotate(
        StockOnHand.objects.filter(
            tenant_id=context.tenant_id,
            location_id=location_id,
            item_id__in=list(item_ids),
        ).select_related("lot"),
        today=timezone.localdate(),
        alert_days=options["expiry_alert_days"],
        notice_days=options["expiry_notice_days"],
    )
    comfortable = {states.SUFFICIENT, states.OVERSTOCK}
    best: dict = {}
    refused: set = set()
    for row in rows:
        if states.name_of(row.state_ordinal) not in comfortable:
            refused.add(row.item_id)
            continue
        held = best.get(row.item_id)
        if held is None or row.quantity > held[0]:
            best[row.item_id] = (row.quantity, row.lot)
    return {
        item_id: (lot.lot_code if lot else "", lot.expires_at if lot else None)
        for item_id, (_quantity, lot) in best.items()
        if item_id not in refused
    }


def _comfortable_items(context, location_id):
    """Every reference at one sede whose shelf can take a delivery."""
    held = StockOnHand.objects.filter(
        tenant_id=context.tenant_id, location_id=location_id
    ).values_list("item_id", flat=True)
    return _comfortable_lots(context, location_id, set(held))


def owned_ids(context):
    """Exactly the rows this fixture writes in its guard tables.

    Three of the seven are enumerable from the tenant's own items and sedes,
    because every id they take is a pure function of a natural key: the forecast
    rows, the `source = model` policy rows and the ledger moves each receipt
    caused.

    **The four order and receipt tables are read back, and that is a stated
    weakness rather than a hidden one.** Their ids are minted by the ORM inside
    the product's own service functions, and threading a derived id through
    `purchase_order.generate` and `goods_receipts.confirm` would be a second
    creation path existing only for the seed -- which is the thing the ledger's
    cross-stage rule exists to prevent. What still holds is the guard's real
    protection: the command is confined to tenants whose slug begins `demo-`,
    which no provisioned network can acquire, and S0's identity and audit rows
    are derived and are checked. What is given up is narrower: an order somebody
    created through the product's own screens on a demo tenant no longer refuses
    the next seed run. If that becomes worth closing, the fix is an `id_for`
    hook on those two services, exactly as `ledger.Move.id` already is.

    `stock_on_hand` is **not** among them, for the reason S3 and S4 both state:
    this fixture writes no projection row, so it owns none -- the projection is
    whatever the ledger produced.
    """
    orders = set(
        PurchaseOrder.objects.filter(tenant_id=context.tenant_id).values_list(
            "id", flat=True
        )
    )
    lines = set(
        PurchaseOrderLine.objects.filter(tenant_id=context.tenant_id).values_list(
            "id", flat=True
        )
    )
    from core.models import GoodsReceipt

    locations = list(
        Location.objects.filter(tenant_id=context.tenant_id).values_list(
            "id", flat=True
        )
    )
    stocked = list(
        Item.objects.filter(tenant_id=context.tenant_id, tracks_stock=True).values_list(
            "id", flat=True
        )
    )
    receipts = set(
        GoodsReceipt.objects.filter(tenant_id=context.tenant_id).values_list(
            "id", flat=True
        )
    )
    receipt_lines = set(
        GoodsReceiptLine.objects.filter(tenant_id=context.tenant_id).values_list(
            "id", flat=True
        )
    )
    # Derived rather than read back: a forecast row's id is a pure function of
    # its `(tenant, item, location)` key, so the guard can state exactly the set
    # a refresh would write.
    forecasts = {
        forecast.forecast_id(context.tenant_id, location_id, item_id)
        for location_id in locations
        for item_id in stocked
    }
    policies = {
        forecast.policy_id(context.tenant_id, location_id, item_id)
        for location_id in locations
        for item_id in stocked
    }
    moves = {
        context.uid("stock_moves", f"receipt:{receipt_id}:{line_id}")
        for receipt_id, line_id in GoodsReceiptLine.objects.filter(
            tenant_id=context.tenant_id
        ).values_list("goods_receipt_id", "id")
    }
    return {
        "purchase_orders": orders,
        "purchase_order_lines": lines,
        "goods_receipts": receipts,
        "goods_receipt_lines": receipt_lines,
        "demand_forecasts": forecasts,
        "stock_policies": policies,
        "stock_moves": moves,
    }


register(
    "purchasing",
    tables=(
        "purchase_orders",
        "purchase_order_lines",
        "goods_receipts",
        "goods_receipt_lines",
        "demand_forecasts",
        # Two tables this fixture adds to through another stage's services: the
        # refresh writes `source = model` policy rows, and a confirmed receipt
        # appends moves through S3's ledger. **It creates no lot** -- it
        # receives into lots the sede already holds, which is what keeps S3's
        # own grid at the row count its fixture is tuned to.
        "stock_policies",
        "stock_moves",
    ),
    requires=("counter",),
    build=build,
    owned_ids=owned_ids,
)
