"""S1's endpoints, on the router `core.api` mounts.

Every path carries the `/api/` prefix and is English (§3). Every endpoint runs
behind S0's single permission dependency (§2), inside the pinned transaction
(A1), and every mutation appends to `audit_log` through S0's path (ledger).

**Sede scoping does not apply to any of these** -- the catalog is the network's
(§2, A2) -- and the one place a location appears is a price's own scope.
"""

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from core import audit
from core.catalog import prices, search
from core.grid import DEFAULT_PAGE_SIZE, Page, paginate
from core.middleware import request_id
from core.models import (
    DOCUMENT_TYPES,
    AuditAction,
    Category,
    Customer,
    ImportRun,
    Item,
    ItemBarcode,
    ItemPrice,
    ItemType,
    Location,
    Manufacturer,
    Role,
    Supplier,
    SupplierItem,
)
from core.permissions import any_member, owner_only, owner_or_admin

router = Router()

ItemTypeValue = Literal["product", "service"]
VatClassValue = Literal["excluded", "exempt", "rate_5", "rate_19"]
InvimaStatusValue = Literal["valid", "in_process", "expired", "not_applicable"]
PriceSourceValue = Literal["manual", "imported"]
DocumentTypeValue = Literal["CC", "CE", "NIT", "TI", "PA", "PEP", "PPT", ""]
SortOrder = Literal["asc", "desc"]

#: `all` exists because "activos", "inactivos" and "todos" are three states and
#: a nullable boolean expresses two. The **default is `true`**, deliberately:
#: the catalog combobox S3, S4, S6 and S7 all read is this endpoint, and a till
#: offering a reference the network stopped selling is a worse failure than a
#: grid that needs one click to show it.
ActiveFilter = Literal["true", "false", "all"]


def _elevated(user):
    return user.role in (Role.OWNER, Role.ADMIN, Role.PLATFORM_ADMIN)


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class ManufacturerOut(Schema):
    id: UUID
    name: str
    nit: str
    item_count: int = 0


class ManufacturerIn(Schema):
    name: str
    nit: str = ""


class CategoryOut(Schema):
    id: UUID
    name: str
    parent_id: UUID | None
    parent_name: str | None
    item_count: int = 0


class CategoryIn(Schema):
    name: str
    parent_id: UUID | None = None


class SupplierOut(Schema):
    id: UUID
    nit: str
    name: str
    contact: str
    payment_terms: str
    lead_time_days: int | None
    item_count: int = 0


class SupplierIn(Schema):
    nit: str = ""
    name: str
    contact: str = ""
    payment_terms: str = ""
    lead_time_days: int | None = None


class SupplierItemOut(Schema):
    id: UUID
    supplier_id: UUID
    supplier_name: str
    item_id: UUID
    item_name: str
    supplier_code: str
    #: Absent for a `cashier`: a purchasing figure is not on a counter person's
    #: screen (§B.8.3).
    cost: Decimal | None
    min_order_pack: int
    is_preferred: bool


class SupplierItemIn(Schema):
    supplier_id: UUID
    item_id: UUID
    supplier_code: str = ""
    cost: Decimal | None = None
    min_order_pack: int = 1
    is_preferred: bool = False


class SupplierItemPatch(Schema):
    supplier_code: str | None = None
    cost: Decimal | None = None
    min_order_pack: int | None = None
    is_preferred: bool | None = None


class BarcodeIn(Schema):
    code: str
    is_primary: bool = False


class BarcodeOut(Schema):
    id: UUID
    code: str
    is_primary: bool


class PriceOut(Schema):
    id: UUID
    location_id: UUID | None
    location_name: str | None
    price: Decimal
    effective_from: date
    effective_to: date | None
    source: PriceSourceValue
    proposal_id: UUID | None
    set_by_user_id: UUID | None
    set_by_name: str | None
    #: In force at this scope today, by the resolution rule. Computed at read
    #: time -- there is no activation job.
    current: bool


class PriceIn(Schema):
    price: Decimal
    effective_from: date | None = None
    location_id: UUID | None = None
    #: Carried in the schema from S1 so S7 extends this endpoint rather than
    #: shipping a second one. **Refused while `price_proposals` does not
    #: exist**, rather than storing an id that names nothing.
    proposal_id: UUID | None = None


class ItemRow(Schema):
    id: UUID
    type: ItemTypeValue
    name: str
    presentation: str
    manufacturer_id: UUID | None
    manufacturer_name: str | None
    category_id: UUID | None
    category_name: str | None
    invima_registration: str
    invima_status: InvimaStatusValue
    invima_expires_at: date | None
    vat_class: VatClassValue
    unit: str
    splittable: bool
    units_per_pack: int
    tracks_stock: bool
    active: bool
    #: The network-wide price in force today. A sede override is shown in the
    #: item panel, where its scope can be named.
    price: Decimal | None


class ItemDetail(ItemRow):
    description: str
    active_ingredient: str
    strength: str
    requires_prescription: bool
    controlled: bool
    cold_chain: bool
    tracks_lots: bool
    tracks_expiry: bool
    custom: dict
    external_code: str
    #: Absent for a `cashier`, with `supplier_items.cost`.
    service_cost: Decimal | None
    #: Empty at S1 and until S7 loads a cap. **Null means unknown, never
    #: uncapped** -- the editor states that beside the field.
    regulated_max_price: Decimal | None
    cap_status: str
    barcodes: list[BarcodeOut]
    supplier_items: list[SupplierItemOut]
    prices: list[PriceOut]


class ItemIn(Schema):
    type: ItemTypeValue
    name: str
    #: Required and with no default. Defaulting to `excluded` -- the
    #: statistically right answer for a medicine -- silently under-charges IVA
    #: on every cosmetic, drink and device, which is a DIAN problem measured in
    #: sanctions (§8) rather than a data-quality problem.
    vat_class: VatClassValue
    unit: str
    invima_status: InvimaStatusValue = "not_applicable"
    description: str = ""
    manufacturer_id: UUID | None = None
    category_id: UUID | None = None
    presentation: str = ""
    active_ingredient: str = ""
    strength: str = ""
    invima_registration: str = ""
    invima_expires_at: date | None = None
    requires_prescription: bool = False
    controlled: bool = False
    cold_chain: bool = False
    splittable: bool = False
    units_per_pack: int = 1
    tracks_stock: bool = True
    tracks_lots: bool = True
    tracks_expiry: bool = True
    active: bool = True
    custom: dict = {}
    external_code: str = ""
    service_cost: Decimal | None = None
    barcodes: list[BarcodeIn] = []


class ItemPatch(Schema):
    type: ItemTypeValue | None = None
    name: str | None = None
    vat_class: VatClassValue | None = None
    unit: str | None = None
    invima_status: InvimaStatusValue | None = None
    description: str | None = None
    manufacturer_id: UUID | None = None
    category_id: UUID | None = None
    clear_manufacturer: bool | None = None
    clear_category: bool | None = None
    presentation: str | None = None
    active_ingredient: str | None = None
    strength: str | None = None
    invima_registration: str | None = None
    invima_expires_at: date | None = None
    clear_invima_expires_at: bool | None = None
    requires_prescription: bool | None = None
    controlled: bool | None = None
    cold_chain: bool | None = None
    splittable: bool | None = None
    units_per_pack: int | None = None
    tracks_stock: bool | None = None
    tracks_lots: bool | None = None
    tracks_expiry: bool | None = None
    active: bool | None = None
    custom: dict | None = None
    external_code: str | None = None
    service_cost: Decimal | None = None
    #: The whole set, replaced. There is no separate barcode endpoint: a barcode
    #: has no life outside its item.
    barcodes: list[BarcodeIn] | None = None


class CatalogSummaryOut(Schema):
    """The filter bar's provenance line and the footer's annotation.

    Both are **tenant-wide and ignore the active filters**, exactly as the
    handoff draws `312 requieren acción` beside `1-15 de 4.284`: they describe
    the catalog, not the view. A count that moved with the filters would be a
    different number every keystroke and would answer no question.
    """

    active_items: int
    services: int
    expired_registrations: int


class CustomerOut(Schema):
    id: UUID
    document_type: DocumentTypeValue
    document: str
    name: str
    phone: str
    email: str
    address: str
    data_consent: bool
    data_consent_at: str | None
    notes: str
    #: Derived from the absent name and document, never stored. A flag would be
    #: a second truth to keep in step with the empty fields.
    erased: bool


class CustomerIn(Schema):
    document_type: DocumentTypeValue = ""
    document: str = ""
    name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    data_consent: bool = False
    notes: str = ""


class CustomerPatch(Schema):
    document_type: DocumentTypeValue | None = None
    document: str | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    data_consent: bool | None = None
    notes: str | None = None


class CustomerDeleteOut(Schema):
    """An administrator who pressed one button is owed the difference."""

    outcome: Literal["deleted", "erased"]
    sale_count: int
    detail: str


class ImportOut(Schema):
    id: UUID
    kind: str
    source: str
    status: str
    dry_run: bool
    started_at: str
    finished_at: str | None
    rows_read: int
    rows_created: int
    rows_updated: int
    rows_failed: int
    errors: list
    started_by_name: str | None


class PriceWithdrawOut(Schema):
    outcome: Literal["deleted", "closed"]
    detail: str


# ---------------------------------------------------------------------------
# Serialisers
# ---------------------------------------------------------------------------


def _manufacturer_out(row):
    return {
        "id": row.id,
        "name": row.name,
        "nit": row.nit,
        "item_count": getattr(row, "item_count", 0),
    }


def _category_out(row):
    return {
        "id": row.id,
        "name": row.name,
        "parent_id": row.parent_id,
        "parent_name": row.parent.name if row.parent_id else None,
        "item_count": getattr(row, "item_count", 0),
    }


def _supplier_out(row):
    return {
        "id": row.id,
        "nit": row.nit,
        "name": row.name,
        "contact": row.contact,
        "payment_terms": row.payment_terms,
        "lead_time_days": row.lead_time_days,
        "item_count": getattr(row, "item_count", 0),
    }


def _supplier_item_out(row, *, with_cost):
    return {
        "id": row.id,
        "supplier_id": row.supplier_id,
        "supplier_name": row.supplier.name,
        "item_id": row.item_id,
        "item_name": row.item.name,
        "supplier_code": row.supplier_code,
        "cost": row.cost if with_cost else None,
        "min_order_pack": row.min_order_pack,
        "is_preferred": row.is_preferred,
    }


def _price_out(row, current_ids, *, with_author=True):
    """One price row. `with_author` is false for a `cashier`, who reads the
    figure in force and not who arrived at it -- the same line §B.8.3 draws
    around cost."""
    return {
        "id": row.id,
        "location_id": row.location_id,
        "location_name": row.location.name if row.location_id else None,
        "price": row.price,
        "effective_from": row.effective_from,
        "effective_to": row.effective_to,
        "source": row.source,
        "proposal_id": row.proposal_id,
        "set_by_user_id": row.set_by_user_id if with_author else None,
        # The stamp, not the join: the person may have been hard-deleted, and a
        # `manual` price that lost its name would read as one nobody typed.
        "set_by_name": (row.set_by_name or None) if with_author else None,
        "current": row.id in current_ids,
    }


def _item_row(row):
    return {
        "id": row.id,
        "type": row.type,
        "name": row.name,
        "presentation": row.presentation,
        "manufacturer_id": row.manufacturer_id,
        "manufacturer_name": row.manufacturer.name if row.manufacturer_id else None,
        "category_id": row.category_id,
        "category_name": row.category.name if row.category_id else None,
        "invima_registration": row.invima_registration,
        "invima_status": row.invima_status,
        "invima_expires_at": row.invima_expires_at,
        "vat_class": row.vat_class,
        "unit": row.unit,
        "splittable": row.splittable,
        "units_per_pack": row.units_per_pack,
        "tracks_stock": row.tracks_stock,
        "active": row.active,
        "price": getattr(row, "network_price", None),
    }


def _item_detail(item, *, with_cost):
    rows, current = prices.history(item)
    current_ids = {row.id for row in current.values()}
    # §B.8.3 · a `cashier` reaches this endpoint and the price **history** is
    # `owner`/`admin`, so they get the rows in force per scope and nothing else.
    # Otherwise the gate on `GET /api/items/{id}/prices` would be one join away
    # from meaningless: every closed row, its source and the name of the person
    # who set it.
    visible = rows if with_cost else list(current.values())
    links = (
        SupplierItem.objects.filter(item=item)
        .select_related("supplier", "item")
        .order_by("-is_preferred", "supplier__name")
    )
    return {
        **_item_row(item),
        "price": _network_price(rows, current),
        "description": item.description,
        "active_ingredient": item.active_ingredient,
        "strength": item.strength,
        "requires_prescription": item.requires_prescription,
        "controlled": item.controlled,
        "cold_chain": item.cold_chain,
        "tracks_lots": item.tracks_lots,
        "tracks_expiry": item.tracks_expiry,
        "custom": item.custom or {},
        "external_code": item.external_code,
        "service_cost": item.service_cost if with_cost else None,
        "regulated_max_price": item.regulated_max_price,
        "cap_status": item.cap_status,
        "barcodes": [
            {"id": one.id, "code": one.code, "is_primary": one.is_primary}
            for one in item.barcodes.all()
        ],
        "supplier_items": [
            _supplier_item_out(one, with_cost=with_cost) for one in links
        ],
        "prices": [
            _price_out(one, current_ids, with_author=with_cost) for one in visible
        ],
    }


def _network_price(rows, current):
    del rows
    row = current.get(None)
    return row.price if row else None


def _customer_out(row):
    return {
        "id": row.id,
        "document_type": row.document_type,
        "document": row.document,
        "name": row.name,
        "phone": row.phone,
        "email": row.email,
        "address": row.address,
        "data_consent": row.data_consent,
        "data_consent_at": (
            row.data_consent_at.isoformat() if row.data_consent_at else None
        ),
        "notes": row.notes,
        "erased": row.erased,
    }


def _import_out(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "source": row.source,
        "status": row.status,
        "dry_run": row.dry_run,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "rows_read": row.rows_read,
        "rows_created": row.rows_created,
        "rows_updated": row.rows_updated,
        "rows_failed": row.rows_failed,
        "errors": row.errors or [],
        "started_by_name": (
            row.started_by_user.name if row.started_by_user_id else None
        ),
    }


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

ITEM_SORTS = {
    "name": ["name", "presentation"],
    "manufacturer": ["manufacturer__name", "name"],
    "category": ["category__name", "name"],
    "presentation": ["presentation", "name"],
    "invima_registration": ["invima_registration", "name"],
    "invima_expires_at": ["invima_expires_at", "name"],
    "invima_status": ["invima_status", "name"],
    "price": ["network_price", "name"],
    "type": ["type", "name"],
}


def _items_for(request):
    return (
        Item.objects.filter(tenant_id=request.tenant_id)
        .select_related("manufacturer", "category")
        .annotate(network_price=prices.network_price_subquery(prices.today()))
    )


@router.get("/items", response=Page[ItemRow], auth=any_member)
def list_items(
    request,
    q: str | None = Query(None),
    type: ItemTypeValue | None = Query(None),
    manufacturer_id: UUID | None = Query(None),
    category_id: UUID | None = Query(None),
    invima_status: InvimaStatusValue | None = Query(None),
    active: ActiveFilter = Query("true"),
    barcode: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("asc"),
):
    """The catalog grid, server-paginated and server-sorted (§9).

    A `cashier` reads it and writes nothing. Sede scoping does not apply: the
    catalog is the network's (§2, A2).
    """
    queryset = _items_for(request)
    if barcode:
        # An exact scan. It resolves to one item or to none, and it is the path
        # S4's till depends on at 50ms.
        queryset = queryset.filter(
            barcodes__code=barcode.strip(), barcodes__tenant_id=request.tenant_id
        )
    queryset = search.matching(queryset, q, request.tenant_id)
    if type:
        queryset = queryset.filter(type=type)
    if manufacturer_id:
        queryset = queryset.filter(manufacturer_id=manufacturer_id)
    if category_id:
        queryset = queryset.filter(
            Q(category_id=category_id) | Q(category__parent_id=category_id)
        )
    if invima_status:
        queryset = queryset.filter(invima_status=invima_status)
    if active != "all":
        queryset = queryset.filter(active=active == "true")

    queryset = queryset.order_by("name", "presentation")
    rows, row_count, page, page_size = paginate(
        queryset,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable=ITEM_SORTS,
    )
    return {
        "rows": [_item_row(row) for row in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


@router.get("/items/summary", response=CatalogSummaryOut, auth=any_member)
def catalog_summary(request):
    """`4.284 referencias activas · 12 servicios`, and `18 con registro
    vencido`. Three counts over the tenant, in one round trip."""
    rows = Item.objects.filter(tenant_id=request.tenant_id)
    return {
        "active_items": rows.filter(active=True).count(),
        "services": rows.filter(active=True, type=ItemType.SERVICE).count(),
        "expired_registrations": rows.filter(
            active=True, invima_status="expired"
        ).count(),
    }


@router.get("/items/{item_id}", response=ItemDetail, auth=any_member)
def read_item(request, item_id: UUID):
    """One item with its barcodes, its supplier links and its prices per scope.

    A `cashier` reads it without `supplier_items.cost` or `service_cost` --
    purchasing figures are not on a counter person's screen (§B.8.3).
    """
    item = get_object_or_404(
        Item.objects.select_related("manufacturer", "category"),
        id=item_id,
        tenant_id=request.tenant_id,
    )
    return _item_detail(item, with_cost=_elevated(request.user))


@router.post("/items", response=ItemDetail, auth=owner_or_admin)
def create_item(request, payload: ItemIn):
    """Create a product or a service, in the same editor (A7).

    **It writes no price.** A11 is a structural property only while
    `item_prices` has one interactive writer, and "one" has to mean one endpoint
    -- a create that also priced would be the second, however carefully it
    routed through the same service. The panel saves the product and then posts
    its opening price to `/api/items/{id}/prices`, which is one press for the
    person and two calls on the wire.
    """
    item = Item(tenant_id=request.tenant_id)
    _apply_item(request, item, payload.dict(exclude_unset=False), creating=True)
    _save_item(item)

    _replace_barcodes(request, item, payload.barcodes)

    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.CREATE,
        entity_type="items",
        entity_id=item.id,
        after=_item_snapshot(item),
        request_id=request_id.get(),
    )
    item.refresh_from_db()
    return _item_detail(item, with_cost=True)


@router.patch("/items/{item_id}", response=ItemDetail, auth=owner_or_admin)
def patch_item(request, item_id: UUID, payload: ItemPatch):
    """Edit the item, including its barcode set as an array.

    There is no `DELETE /api/items/{id}` and there will not be one: every later
    table in the product references `items`, and a hard-deleted item is a hole
    in a ticket, a stock ledger and a fiscal record. `active = false` is the only
    removal, and §2's hard-delete right applies to users, not to catalog rows.
    """
    item = get_object_or_404(Item, id=item_id, tenant_id=request.tenant_id)
    before = _item_snapshot(item)
    _apply_item(request, item, payload.dict(exclude_unset=True), creating=False)
    _save_item(item)
    if payload.barcodes is not None:
        _replace_barcodes(request, item, payload.barcodes)

    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="items",
        entity_id=item.id,
        before=before,
        after=_item_snapshot(item),
        request_id=request_id.get(),
    )
    item.refresh_from_db()
    return _item_detail(item, with_cost=True)


ITEM_FIELDS = (
    "type",
    "name",
    "description",
    "presentation",
    "active_ingredient",
    "strength",
    "invima_registration",
    "invima_expires_at",
    "invima_status",
    "requires_prescription",
    "controlled",
    "cold_chain",
    "unit",
    "splittable",
    "units_per_pack",
    "vat_class",
    "tracks_stock",
    "tracks_lots",
    "tracks_expiry",
    "active",
    "custom",
    "external_code",
    "service_cost",
)


def _apply_item(request, item, data, *, creating):
    for field in ITEM_FIELDS:
        if field in data and data[field] is not None:
            setattr(item, field, data[field])
    # The one field whose null is a value rather than an omission: clearing a
    # service's cost of goods is how a network says it has none, and a PATCH
    # that dropped it would report success and change nothing.
    if "service_cost" in data:
        item.service_cost = data["service_cost"]
    if creating:
        item.custom = data.get("custom") or {}

    if data.get("clear_invima_expires_at"):
        item.invima_expires_at = None
    if data.get("clear_manufacturer"):
        item.manufacturer = None
    elif data.get("manufacturer_id"):
        item.manufacturer = _related(
            Manufacturer, data["manufacturer_id"], request, "Ese laboratorio"
        )
    if data.get("clear_category"):
        item.category = None
    elif data.get("category_id"):
        item.category = _related(
            Category, data["category_id"], request, "Esa categoría"
        )

    if not (item.name or "").strip():
        raise HttpError(422, "Escriba el nombre del producto.")
    if not (item.unit or "").strip():
        raise HttpError(422, "Escriba la unidad base del producto.")

    # A service is a row whose `tracks_stock` is false, and everything the
    # switch makes meaningless is normalised here rather than left as stale data
    # somebody later trusts.
    if item.type == ItemType.SERVICE:
        item.tracks_stock = False
        item.manufacturer = None
        item.presentation = ""
        item.active_ingredient = ""
        item.strength = ""
        item.invima_registration = ""
        item.invima_expires_at = None
        item.invima_status = "not_applicable"
        item.requires_prescription = False
        item.controlled = False
        item.cold_chain = False
        item.splittable = False
        item.units_per_pack = 1
    else:
        item.service_cost = None
    if not item.tracks_stock:
        item.tracks_lots = False
        item.tracks_expiry = False


def _save_item(item):
    try:
        item.save()
    except IntegrityError as error:
        raise _item_conflict(error) from error


def _item_conflict(error):
    text = str(error)
    if "one_item_per_name_and_presentation_per_tenant" in text:
        return HttpError(
            409,
            "Ya hay un producto con ese nombre y esa presentación. Dos filas "
            "para la misma presentación del mismo producto es justo lo que una "
            "depuración de catálogo existe para quitar.",
        )
    if "one_item_external_code_per_tenant" in text:
        return HttpError(409, "Ya hay un producto con ese código externo.")
    if "splittable_pack_holds_more_than_one" in text:
        return HttpError(
            422,
            "Un producto fraccionable necesita más de una unidad base por empaque.",
        )
    if "units_per_pack_is_at_least_one" in text:
        return HttpError(422, "Las unidades por empaque no pueden ser menos de 1.")
    if "untracked_item_moves_no_lots" in text:
        return HttpError(
            422,
            "Un servicio no maneja lotes ni vencimiento: no mueve existencias.",
        )
    return HttpError(409, "No pudimos guardar este producto con esos valores.")


def _item_snapshot(item):
    return {
        # The codes are in the snapshot because they are edited through this
        # endpoint: without them a barcode-only save writes an audit row whose
        # before and after are identical, and the trail cannot say who moved a
        # scan from one product to another.
        "barcodes": sorted(
            f"{code}{'*' if primary else ''}"
            for code, primary in ItemBarcode.objects.filter(item=item).values_list(
                "code", "is_primary"
            )
        ),
        "name": item.name,
        "type": item.type,
        "presentation": item.presentation,
        "vat_class": item.vat_class,
        "invima_registration": item.invima_registration,
        "invima_status": item.invima_status,
        "invima_expires_at": (
            item.invima_expires_at.isoformat() if item.invima_expires_at else None
        ),
        "unit": item.unit,
        "splittable": item.splittable,
        "units_per_pack": item.units_per_pack,
        "tracks_stock": item.tracks_stock,
        "tracks_lots": item.tracks_lots,
        "tracks_expiry": item.tracks_expiry,
        "active": item.active,
        "manufacturer_id": str(item.manufacturer_id) if item.manufacturer_id else None,
        "category_id": str(item.category_id) if item.category_id else None,
        "service_cost": str(item.service_cost) if item.service_cost else None,
    }


def _related(model, value, request, noun):
    row = model.objects.filter(id=value, tenant_id=request.tenant_id).first()
    if row is None:
        raise HttpError(422, f"{noun} no existe en esta droguería.")
    return row


def _replace_barcodes(request, item, barcodes):
    """The whole set, replaced, with exactly one `Principal`.

    A code already held by another item is refused **naming that item**, not
    with a uniqueness message: a cashier's scan must resolve to one item in
    under 50ms (§4), and an ambiguous scan sells the wrong product at the wrong
    price.
    """
    codes: list[tuple[str, bool]] = []
    for entry in barcodes:
        code = entry.code.strip()
        if not code:
            continue
        if code in [one for one, _ in codes]:
            raise HttpError(422, f"El código {code} está repetido en esta lista.")
        codes.append((code, entry.is_primary))

    taken = (
        ItemBarcode.objects.filter(
            tenant_id=request.tenant_id, code__in=[code for code, _ in codes]
        )
        .exclude(item=item)
        .select_related("item")
        .first()
    )
    if taken is not None:
        raise HttpError(
            409,
            f"El código {taken.code} ya es de «{taken.item.name}». Un código de "
            "barras identifica un solo producto.",
        )

    if codes and not any(primary for _, primary in codes):
        codes[0] = (codes[0][0], True)
    primaries = [code for code, primary in codes if primary]
    if len(primaries) > 1:
        raise HttpError(422, "Solo un código de barras puede ser el principal.")

    ItemBarcode.objects.filter(item=item).exclude(
        code__in=[code for code, _ in codes]
    ).delete()
    existing = {row.code: row for row in ItemBarcode.objects.filter(item=item)}
    for code, primary in codes:
        row = existing.get(code)
        if row is None:
            ItemBarcode.objects.create(
                tenant_id=request.tenant_id,
                item=item,
                code=code,
                is_primary=primary,
            )
        elif row.is_primary != primary:
            # Clearing first: the partial unique index admits one primary per
            # item, and swapping which one it is has to pass through zero.
            ItemBarcode.objects.filter(item=item, is_primary=True).update(
                is_primary=False
            )
            row.is_primary = primary
            row.save(update_fields=["is_primary", "updated_at"])


# ---------------------------------------------------------------------------
# Prices — the one write path (A11)
# ---------------------------------------------------------------------------


@router.get("/items/{item_id}/prices", response=list[PriceOut], auth=owner_or_admin)
def list_prices(request, item_id: UUID):
    """Every price row for the item, per scope, with its window and `source`."""
    item = get_object_or_404(Item, id=item_id, tenant_id=request.tenant_id)
    rows, current = prices.history(item)
    current_ids = {row.id for row in current.values()}
    return [_price_out(row, current_ids) for row in rows]


@router.post("/items/{item_id}/prices", response=PriceOut, auth=owner_or_admin)
def create_price(request, item_id: UUID, payload: PriceIn):
    """**The only endpoint in the product that writes a price** (A11).

    Creates a row and closes the open row in the same scope in the same
    transaction. `source` is `manual` from this endpoint always and
    `set_by_user_id` is the caller -- neither is accepted from the body.

    S7 extends this endpoint. It does not ship a second one: a bulk apply, a
    scheduled repricing or an `Aplicar todas` restores precisely the failure A11
    removed -- a model's number reaching every till without a person having
    typed or confirmed it.
    """
    item = get_object_or_404(Item, id=item_id, tenant_id=request.tenant_id)
    if payload.proposal_id is not None:
        # The column exists and is nullable from S1; the producer does not.
        # Storing an id that names nothing would be worse than refusing it.
        raise HttpError(
            422,
            "Todavía no hay propuestas de precio en Botica. El módulo de "
            "Precios llega en una etapa posterior.",
        )
    location = None
    if payload.location_id is not None:
        location = _related(Location, payload.location_id, request, "Esa sede")

    row = prices.set_price(
        item=item,
        actor=request.user,
        tenant_id=request.tenant_id,
        price=payload.price,
        effective_from=payload.effective_from,
        location=location,
        request_id=request_id.get(),
    )
    _, current = prices.history(item)
    return _price_out(row, {one.id for one in current.values()})


@router.delete(
    "/item-prices/{price_id}", response=PriceWithdrawOut, auth=owner_or_admin
)
def withdraw_price(request, price_id: UUID):
    """Take a price row out of play, and say which of the two things happened.

    A future-dated row that was never in force is removed; a row that has been
    in force is closed instead, because it is what a past sale was made at. A
    closed sede override returns that sede to the network price by the
    resolution rule, with no further edit.
    """
    row = get_object_or_404(
        ItemPrice.objects.select_related("item"),
        id=price_id,
        tenant_id=request.tenant_id,
    )
    outcome = prices.withdraw_price(
        row=row,
        actor=request.user,
        tenant_id=request.tenant_id,
        request_id=request_id.get(),
    )
    return {
        "outcome": outcome,
        "detail": (
            "Se quitó el precio, que nunca estuvo vigente."
            if outcome == "deleted"
            else "Se cerró el precio de esta sede. La sede vuelve al precio de red."
        ),
    }


# ---------------------------------------------------------------------------
# Laboratorios y categorías
# ---------------------------------------------------------------------------


@router.get("/manufacturers", response=list[ManufacturerOut], auth=any_member)
def list_manufacturers(request):
    """The laboratorios, with the reference count the settings section links
    into the catalog grid with."""
    rows = (
        Manufacturer.objects.filter(tenant_id=request.tenant_id)
        .annotate(item_count=Count("items"))
        .order_by("name")
    )
    return [_manufacturer_out(row) for row in rows]


@router.post("/manufacturers", response=ManufacturerOut, auth=owner_or_admin)
def create_manufacturer(request, payload: ManufacturerIn):
    return _create_named(
        request,
        Manufacturer,
        {"name": payload.name.strip(), "nit": payload.nit.strip()},
        "manufacturers",
        "Ya hay un laboratorio con ese nombre.",
        _manufacturer_out,
    )


@router.patch(
    "/manufacturers/{manufacturer_id}", response=ManufacturerOut, auth=owner_or_admin
)
def patch_manufacturer(request, manufacturer_id: UUID, payload: ManufacturerIn):
    row = get_object_or_404(
        Manufacturer, id=manufacturer_id, tenant_id=request.tenant_id
    )
    return _patch_named(
        request,
        row,
        {"name": payload.name.strip(), "nit": payload.nit.strip()},
        "manufacturers",
        "Ya hay un laboratorio con ese nombre.",
        _manufacturer_out,
    )


@router.delete("/manufacturers/{manufacturer_id}", auth=owner_only)
def delete_manufacturer(request, manufacturer_id: UUID):
    row = get_object_or_404(
        Manufacturer, id=manufacturer_id, tenant_id=request.tenant_id
    )
    used = Item.objects.filter(manufacturer=row).count()
    if used:
        raise HttpError(
            409,
            f"{row.name} está en {used} referencia{'s' if used != 1 else ''} del "
            "catálogo. Cámbielas de laboratorio antes de eliminarlo.",
        )
    return _delete_named(request, row, "manufacturers", {"name": row.name})


@router.get("/categories", response=list[CategoryOut], auth=any_member)
def list_categories(request):
    """Two levels, in reading order: a parent, then its children."""
    rows = (
        Category.objects.filter(tenant_id=request.tenant_id)
        .select_related("parent")
        .annotate(item_count=Count("items"))
        .order_by("parent__name", "name")
    )
    ordered = sorted(
        rows,
        key=lambda row: (
            (row.parent.name if row.parent else row.name).lower(),
            1 if row.parent_id else 0,
            row.name.lower(),
        ),
    )
    return [_category_out(row) for row in ordered]


@router.post("/categories", response=CategoryOut, auth=owner_or_admin)
def create_category(request, payload: CategoryIn):
    parent = _category_parent(request, payload.parent_id)
    return _create_named(
        request,
        Category,
        {"name": payload.name.strip(), "parent": parent},
        "categories",
        "Ya hay una categoría con ese nombre en ese nivel.",
        _category_out,
    )


@router.patch("/categories/{category_id}", response=CategoryOut, auth=owner_or_admin)
def patch_category(request, category_id: UUID, payload: CategoryIn):
    row = get_object_or_404(
        Category.objects.select_related("parent"),
        id=category_id,
        tenant_id=request.tenant_id,
    )
    parent = _category_parent(request, payload.parent_id)
    if parent is not None and parent.id == row.id:
        raise HttpError(422, "Una categoría no puede ser su propia madre.")
    if parent is not None and Category.objects.filter(parent=row).exists():
        raise HttpError(
            422,
            "Esta categoría ya tiene subcategorías, así que no puede volverse "
            "subcategoría de otra. El catálogo tiene dos niveles.",
        )
    return _patch_named(
        request,
        row,
        {"name": payload.name.strip(), "parent": parent},
        "categories",
        "Ya hay una categoría con ese nombre en ese nivel.",
        _category_out,
    )


def _category_parent(request, parent_id):
    if parent_id is None:
        return None
    parent = _related(Category, parent_id, request, "Esa categoría")
    if parent.parent_id is not None:
        raise HttpError(
            422,
            "El catálogo tiene dos niveles: una subcategoría no puede colgar de "
            "otra subcategoría.",
        )
    return parent


@router.delete("/categories/{category_id}", auth=owner_only)
def delete_category(request, category_id: UUID):
    row = get_object_or_404(Category, id=category_id, tenant_id=request.tenant_id)
    used = Item.objects.filter(category=row).count()
    children = Category.objects.filter(parent=row).count()
    if used or children:
        parts = []
        if used:
            parts.append(f"{used} referencia{'s' if used != 1 else ''}")
        if children:
            parts.append(f"{children} subcategoría{'s' if children != 1 else ''}")
        raise HttpError(
            409,
            f"{row.name} tiene {' y '.join(parts)}. Muévalas antes de eliminarla.",
        )
    return _delete_named(request, row, "categories", {"name": row.name})


# ---------------------------------------------------------------------------
# Proveedores
# ---------------------------------------------------------------------------


@router.get("/suppliers", response=list[SupplierOut], auth=owner_or_admin)
def list_suppliers(request):
    rows = (
        Supplier.objects.filter(tenant_id=request.tenant_id)
        .annotate(item_count=Count("supplier_items"))
        .order_by("name")
    )
    return [_supplier_out(row) for row in rows]


@router.post("/suppliers", response=SupplierOut, auth=owner_or_admin)
def create_supplier(request, payload: SupplierIn):
    return _create_named(
        request,
        Supplier,
        _supplier_fields(payload),
        "suppliers",
        "Ya hay un proveedor con ese NIT.",
        _supplier_out,
    )


@router.patch("/suppliers/{supplier_id}", response=SupplierOut, auth=owner_or_admin)
def patch_supplier(request, supplier_id: UUID, payload: SupplierIn):
    row = get_object_or_404(Supplier, id=supplier_id, tenant_id=request.tenant_id)
    return _patch_named(
        request,
        row,
        _supplier_fields(payload),
        "suppliers",
        "Ya hay un proveedor con ese NIT.",
        _supplier_out,
    )


def _supplier_fields(payload):
    if not payload.name.strip():
        raise HttpError(422, "Escriba el nombre del proveedor.")
    return {
        "nit": payload.nit.strip(),
        "name": payload.name.strip(),
        "contact": payload.contact.strip(),
        "payment_terms": payload.payment_terms.strip(),
        "lead_time_days": payload.lead_time_days,
    }


@router.delete("/suppliers/{supplier_id}", auth=owner_only)
def delete_supplier(request, supplier_id: UUID):
    row = get_object_or_404(Supplier, id=supplier_id, tenant_id=request.tenant_id)
    used = SupplierItem.objects.filter(supplier=row).count()
    if used:
        raise HttpError(
            409,
            f"{row.name} surte {used} referencia{'s' if used != 1 else ''}. "
            "Quite esas referencias del proveedor antes de eliminarlo.",
        )
    return _delete_named(request, row, "suppliers", {"name": row.name, "nit": row.nit})


@router.get("/supplier-items", response=list[SupplierItemOut], auth=owner_or_admin)
def list_supplier_items(
    request,
    supplier_id: UUID | None = Query(None),
    item_id: UUID | None = Query(None),
):
    """The supplier↔item links. Read by item from the item editor, by supplier
    from the settings section, and by item again by S6."""
    rows = (
        SupplierItem.objects.filter(tenant_id=request.tenant_id)
        .select_related("supplier", "item")
        .order_by("-is_preferred", "supplier__name")
    )
    if supplier_id:
        rows = rows.filter(supplier_id=supplier_id)
    if item_id:
        rows = rows.filter(item_id=item_id)
    return [_supplier_item_out(row, with_cost=True) for row in rows]


@router.post("/supplier-items", response=SupplierItemOut, auth=owner_or_admin)
def create_supplier_item(request, payload: SupplierItemIn):
    supplier = _related(Supplier, payload.supplier_id, request, "Ese proveedor")
    item = _related(Item, payload.item_id, request, "Ese producto")
    if item.type == ItemType.SERVICE:
        raise HttpError(422, "Un servicio no se compra a un proveedor.")
    row = SupplierItem(
        tenant_id=request.tenant_id,
        supplier=supplier,
        item=item,
        supplier_code=payload.supplier_code.strip(),
        cost=payload.cost,
        min_order_pack=max(1, payload.min_order_pack),
        is_preferred=payload.is_preferred,
    )
    try:
        with transaction.atomic():
            if payload.is_preferred:
                _clear_preferred(item)
            row.save()
    except IntegrityError as error:
        raise HttpError(
            409, "Ese proveedor ya está enlazado a este producto."
        ) from error
    _audit_link(request, AuditAction.CREATE, row, after=_link_snapshot(row))
    return _supplier_item_out(row, with_cost=True)


@router.patch(
    "/supplier-items/{link_id}", response=SupplierItemOut, auth=owner_or_admin
)
def patch_supplier_item(request, link_id: UUID, payload: SupplierItemPatch):
    row = get_object_or_404(
        SupplierItem.objects.select_related("supplier", "item"),
        id=link_id,
        tenant_id=request.tenant_id,
    )
    before = _link_snapshot(row)
    data = payload.dict(exclude_unset=True)
    if "supplier_code" in data and data["supplier_code"] is not None:
        row.supplier_code = data["supplier_code"].strip()
    if "cost" in data:
        row.cost = data["cost"]
    if data.get("min_order_pack") is not None:
        row.min_order_pack = max(1, data["min_order_pack"])
    with transaction.atomic():
        if data.get("is_preferred"):
            # One preferred supplier per item: setting it clears it on the
            # item's other links in the same transaction, or the partial unique
            # index refuses the save.
            _clear_preferred(row.item, except_id=row.id)
            row.is_preferred = True
        elif data.get("is_preferred") is False:
            row.is_preferred = False
        row.save()
    _audit_link(
        request, AuditAction.UPDATE, row, before=before, after=_link_snapshot(row)
    )
    return _supplier_item_out(row, with_cost=True)


@router.delete("/supplier-items/{link_id}", auth=owner_or_admin)
def delete_supplier_item(request, link_id: UUID):
    row = get_object_or_404(
        SupplierItem.objects.select_related("supplier", "item"),
        id=link_id,
        tenant_id=request.tenant_id,
    )
    before = _link_snapshot(row)
    row.delete()
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.DELETE,
        entity_type="supplier_items",
        entity_id=link_id,
        before=before,
        request_id=request_id.get(),
    )
    return {"deleted": True}


def _clear_preferred(item, except_id=None):
    rows = SupplierItem.objects.filter(item=item, is_preferred=True)
    if except_id:
        rows = rows.exclude(id=except_id)
    rows.update(is_preferred=False)


def _link_snapshot(row):
    return {
        "supplier": row.supplier.name,
        "item": row.item.name,
        "supplier_code": row.supplier_code,
        "cost": str(row.cost) if row.cost is not None else None,
        "min_order_pack": row.min_order_pack,
        "is_preferred": row.is_preferred,
    }


def _audit_link(request, action, row, *, before=None, after=None):
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=action,
        entity_type="supplier_items",
        entity_id=row.id,
        before=before,
        after=after,
        request_id=request_id.get(),
    )


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------


@router.get("/customers", response=Page[CustomerOut], auth=owner_or_admin)
def list_customers(
    request,
    q: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("asc"),
):
    """The clientes. `q` matches document and name."""
    rows = Customer.objects.filter(tenant_id=request.tenant_id)
    if q and q.strip():
        term = q.strip()
        rows = rows.filter(Q(name__icontains=term) | Q(document__icontains=term))
    rows = rows.order_by("name", "document")
    page_rows, row_count, page, page_size = paginate(
        rows,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable={
            "name": ["name"],
            "document": ["document"],
            "phone": ["phone"],
            "email": ["email"],
            "data_consent": ["data_consent"],
        },
    )
    return {
        "rows": [_customer_out(row) for row in page_rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


@router.post("/customers", response=CustomerOut, auth=owner_or_admin)
def create_customer(request, payload: CustomerIn):
    row = Customer(tenant_id=request.tenant_id)
    _apply_customer(row, payload.dict())
    try:
        row.save()
    except IntegrityError as error:
        raise HttpError(
            409, "Ya hay un cliente con ese documento en esta droguería."
        ) from error
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.CREATE,
        entity_type="customers",
        entity_id=row.id,
        after=_customer_snapshot(row),
        request_id=request_id.get(),
    )
    return _customer_out(row)


@router.get("/customers/{customer_id}", response=CustomerOut, auth=owner_or_admin)
def read_customer(request, customer_id: UUID):
    row = get_object_or_404(Customer, id=customer_id, tenant_id=request.tenant_id)
    return _customer_out(row)


@router.patch("/customers/{customer_id}", response=CustomerOut, auth=owner_or_admin)
def patch_customer(request, customer_id: UUID, payload: CustomerPatch):
    """Edit identity and consent. Setting `data_consent` stamps
    `data_consent_at` server-side -- a boolean alone cannot answer *when*, and
    Ley 1581 asks (§7)."""
    row = get_object_or_404(Customer, id=customer_id, tenant_id=request.tenant_id)
    before = _customer_snapshot(row)
    _apply_customer(row, payload.dict(exclude_unset=True))
    try:
        row.save()
    except IntegrityError as error:
        raise HttpError(
            409, "Ya hay un cliente con ese documento en esta droguería."
        ) from error
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="customers",
        entity_id=row.id,
        before=before,
        after=_customer_snapshot(row),
        request_id=request_id.get(),
    )
    return _customer_out(row)


CUSTOMER_FIELDS = (
    "document_type",
    "document",
    "name",
    "phone",
    "email",
    "address",
    "notes",
)


def _apply_customer(row, data):
    for field in CUSTOMER_FIELDS:
        if field in data and data[field] is not None:
            setattr(row, field, str(data[field]).strip())
    if data.get("document") and not row.document_type:
        raise HttpError(422, "Elija el tipo de documento.")
    if row.document_type and row.document_type not in DOCUMENT_TYPES:
        raise HttpError(422, "Ese tipo de documento no existe.")
    if "data_consent" in data and data["data_consent"] is not None:
        consented = bool(data["data_consent"])
        if consented and not row.data_consent:
            row.data_consent_at = timezone.now()
        if not consented:
            row.data_consent_at = None
        row.data_consent = consented


def _customer_snapshot(row):
    return {
        "document_type": row.document_type,
        "document": row.document,
        "name": row.name,
        "phone": row.phone,
        "email": row.email,
        "address": row.address,
        "notes": row.notes,
        "data_consent": row.data_consent,
        "data_consent_at": (
            row.data_consent_at.isoformat() if row.data_consent_at else None
        ),
    }


def _sales_referencing(customer):
    """How many rows in other tables name this customer.

    Discovered from the schema rather than listed here, so that when S4 creates
    `sales` with a `customer_id` the count picks it up without an edit to this
    stage -- which is exactly what "S4 adds `sales` to that count, and it adds
    no column to `customers` doing it" means. At S1 nothing references
    `customers`, so every delete takes the first branch, and **that is the
    assertion rather than a gap in it**.
    """
    total = 0
    for relation in Customer._meta.related_objects:
        manager = relation.related_model._default_manager
        total += manager.filter(**{relation.field.name: customer}).count()
    return total


@router.delete("/customers/{customer_id}", response=CustomerDeleteOut, auth=owner_only)
def delete_customer(request, customer_id: UUID):
    """A Ley 1581 deletion, in the two shapes the data admits.

    A customer no sale references is removed outright -- the ordinary case of a
    row typed wrong at the counter two minutes ago. A customer any `sales` row
    references is **never hard-deleted**: the identifying fields are erased in
    place and the row survives, because S5 builds the acquirer block by reading
    the customer *through the sale* at the moment it hands the sale over, and a
    sale that lost its customer is a ticket whose counterpart in the client's
    invoicing system names a person Botica can no longer produce.

    No column records the erasure. This mutation already writes an `audit_log`
    row carrying the actor, the entity and both before and after, which is what
    answers "who erased this and when"; a flag would be a second truth to keep
    in step with the empty fields.
    """
    row = get_object_or_404(Customer, id=customer_id, tenant_id=request.tenant_id)
    before = _customer_snapshot(row)
    sale_count = _sales_referencing(row)

    if sale_count == 0:
        row.delete()
        audit.record(
            actor=request.user,
            tenant_id=request.tenant_id,
            action=AuditAction.DELETE,
            entity_type="customers",
            entity_id=customer_id,
            before=before,
            request_id=request_id.get(),
        )
        return {
            "outcome": "deleted",
            "sale_count": 0,
            "detail": "Se eliminó el cliente. Ninguna venta lo nombraba.",
        }

    row.document_type = ""
    row.document = ""
    row.name = ""
    row.phone = ""
    row.email = ""
    row.address = ""
    row.notes = ""
    row.data_consent = False
    row.data_consent_at = None
    row.save()
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="customers",
        entity_id=customer_id,
        before=before,
        after=_customer_snapshot(row),
        request_id=request_id.get(),
    )
    return {
        "outcome": "erased",
        "sale_count": sale_count,
        "detail": (
            f"Se borraron los datos del cliente. {sale_count} venta"
            f"{'s' if sale_count != 1 else ''} sigue"
            f"{'n' if sale_count != 1 else ''} nombrándolo y no se tocaron."
        ),
    }


# ---------------------------------------------------------------------------
# Cargas
# ---------------------------------------------------------------------------


@router.get("/imports", response=Page[ImportOut], auth=owner_or_admin)
def list_imports(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
):
    """The load-run log: kind, source, status, counts and the per-row error
    list. Read-only -- runs are created by the command, never by this endpoint.

    It is how an administrator sees what onboarding did to their catalog without
    asking us.
    """
    rows = (
        ImportRun.objects.filter(tenant_id=request.tenant_id)
        .select_related("started_by_user")
        .order_by("-started_at")
    )
    page_rows, row_count, page, page_size = paginate(
        rows,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable={
            "started_at": ["started_at"],
            "kind": ["kind"],
            "status": ["status"],
            "rows_failed": ["rows_failed"],
        },
    )
    return {
        "rows": [_import_out(row) for row in page_rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# The three small CRUD shapes, written once
# ---------------------------------------------------------------------------


def _create_named(request, model, fields, entity_type, conflict, serialise):
    if not str(fields.get("name") or "").strip():
        raise HttpError(422, "Escriba un nombre.")
    row = model(tenant_id=request.tenant_id, **fields)
    try:
        row.save()
    except IntegrityError as error:
        raise HttpError(409, conflict) from error
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.CREATE,
        entity_type=entity_type,
        entity_id=row.id,
        after=_named_snapshot(fields),
        request_id=request_id.get(),
    )
    return serialise(row)


def _patch_named(request, row, fields, entity_type, conflict, serialise):
    if not str(fields.get("name") or "").strip():
        raise HttpError(422, "Escriba un nombre.")
    before = _named_snapshot({field: getattr(row, field) for field in fields})
    for field, value in fields.items():
        setattr(row, field, value)
    try:
        row.save()
    except IntegrityError as error:
        raise HttpError(409, conflict) from error
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type=entity_type,
        entity_id=row.id,
        before=before,
        after=_named_snapshot(fields),
        request_id=request_id.get(),
    )
    return serialise(row)


def _delete_named(request, row, entity_type, before):
    row_id = row.id
    row.delete()
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.DELETE,
        entity_type=entity_type,
        entity_id=row_id,
        before=before,
        request_id=request_id.get(),
    )
    return {"deleted": True}


def _named_snapshot(fields):
    return {
        key: (
            str(value)
            if value is not None and not isinstance(value, (int, bool, str))
            else value
        )
        for key, value in fields.items()
    }
