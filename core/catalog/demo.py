"""The `catalog` fixture, registered with **S0's** `seed_demo_tenant`.

S1 ships no seed command. It registers one fixture, declaring S0's identity
fixture as its one `requires`, and writes its own rows through its own stage's
tables. It **creates no tenant, no `locations` row and no user, in any profile**:
those arrive already built and this fixture reads them.

Why this is a deliverable and not a fixtures file: a platform that needs a data
migration before it can be shown is a platform that cannot be sold (§1). The
seed is what makes the demo, the pilot's first morning and every developer's
local database the same shape, and **a stage is not finished until its screens
render convincingly from it** -- a sharper completion test than a green suite,
because it catches the empty state nobody designed and the tile whose
denominator is zero.

**Ids are derived, not random.** Every row takes a uuid v5 over S0's fixed
namespace and this fixture's own natural key, so a rebuilt seed keeps the ids it
had -- a demo script, a screenshot, a saved link and a bug report all still point
at the same row after somebody resets their database.

**It writes no `audit_log` row.** That table is S0's fixture's to guard, and the
registry fails a run whose fixture writes into a table it did not declare -- so
the price rows here are written directly rather than through the price editor's
own path, which appends to the trail. What that path guarantees and this
reproduces exactly is the shape of the row: `imported` with no author for an
opening price, `manual` carrying the seeded administrator's id for a repricing.
"""

import re
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache

from django.utils import timezone

from core.catalog import vocabulary as vocab
from core.demo.registry import register
from core.models import (
    Category,
    Customer,
    ImportKind,
    ImportRun,
    ImportStatus,
    InvimaStatus,
    Item,
    ItemBarcode,
    ItemPrice,
    ItemType,
    Location,
    Manufacturer,
    PriceSource,
    Role,
    Supplier,
    SupplierItem,
    User,
)

#: 4.284, of which five are services -- which is what makes the drawn grid
#: footer read `1-15 de 4.284` and the pagination end at page **172** at 25 rows
#: a page.
CATALOG_SIZE = 4284

#: What each profile changes, and the fixture answers to all five (§1). The
#: catalog itself is the same catalog in four of them, because a network's
#: product list does not shrink because its history is short or grow because it
#: has more sedes.
PROFILES = {
    # everything: 4.284 items, one open price each, the dated repricings inside
    # the 180-day window, the forty clientes.
    "default": {
        "token_catalog": False,
        "reserved_customers": True,
        "customers": 40,
        "window_days": 180,
        "repriced": 30,
        "overrides_per_location": 2,
        "future_price": True,
    },
    # the same catalog and the same customers, with the repricings **compressed
    # into the twelve-day window** -- a price that moves outside the history S4
    # wrote co-moves with nothing, which is the one thing those rows exist to do.
    "young": {
        "token_catalog": False,
        "reserved_customers": True,
        "customers": 40,
        "window_days": 12,
        "repriced": 30,
        "overrides_per_location": 2,
        "future_price": True,
    },
    # the same catalog, **one open price per item and no dated history at all**.
    # With no sales there is no window for a price to have moved inside, and a
    # price history with no volume behind it is a signal S7 would be right to
    # ignore. No sede override and no future-dated row either, for the same
    # reason: both are closed or pending windows, and this profile has none.
    "cold": {
        "token_catalog": False,
        "reserved_customers": True,
        "customers": 40,
        "window_days": 0,
        "repriced": 0,
        "overrides_per_location": 0,
        "future_price": False,
    },
    # the same 4.284 items -- twenty sedes do not make a network sell more
    # references -- with the per-sede overrides spread across whatever locations
    # exist. That is the only thing a profile changes the size of in this stage,
    # and the price-resolution join is the read that feels it.
    "scale": {
        "token_catalog": False,
        "reserved_customers": True,
        "customers": 40,
        "window_days": 180,
        "repriced": 30,
        "overrides_per_location": 2,
        "future_price": True,
    },
    # a token catalog. It exists to be the tenant another tenant is isolated
    # *from*, and building four thousand rows to prove an isolation check would
    # be a slow way to prove nothing.
    "minimal": {
        "token_catalog": True,
        "reserved_customers": False,
        "customers": 2,
        "window_days": 0,
        "repriced": 0,
        "overrides_per_location": 0,
        "future_price": False,
    },
}

#: The laboratorio `minimal` builds, **whose name appears in no other profile**
#: -- so the load tool's cross-tenant check has a value that can only have come
#: from the other network.
MINIMAL_MANUFACTURER = "Laboratorio Aurora"

#: Three customers reserved by name, because a check that reaches its own state
#: by hand-editing rows is a check nobody runs twice (*Verification*, check 9).
#:
#: `RESERVED_NEVER_REFERENCED` is the row **no fixture in any stage may ever
#: reference** -- it exists so the hard-delete half of the Ley 1581 check has a
#: customer of its own. `RESERVED_FOR_SALES` is the one **S4's fixture hangs
#: sales on**, so the erasure half has one the moment `sales` exists.
#: `RESERVED_ERASED` is **already erased in place**, its identifying fields
#: empty and no flag behind them, so `Cliente eliminado` renders and is provably
#: derived before any sale exists at all.
RESERVED_NEVER_REFERENCED = ("CC", "900000001")
RESERVED_FOR_SALES = ("CC", "900000002")
RESERVED_ERASED = "reserved-erased"

#: Invariant 1, this fixture's share of it. What is on screen reads as real,
#: because a demo whose sidebar says `TENANT DEMO 1` demonstrates nothing; what
#: is in the database does not.
DEMO_PHONE = "+57 601 000 0000"
DEMO_EMAIL_DOMAIN = "example.com"  # RFC 2606

DOCUMENT_TYPES_IN_SEED = ("CC", "CE", "NIT", "TI", "PA", "PEP", "PPT")

FIRST_NAMES = [
    "Beatriz",
    "Andrés",
    "Marcela",
    "Jhon",
    "Liliana",
    "Wilson",
    "Yuly",
    "Diana",
    "Hernán",
    "Camila",
    "Óscar",
    "Natalia",
    "Rubén",
    "Sandra",
    "Álvaro",
    "Gloria",
    "Fabián",
    "Paola",
    "Nelson",
    "Claudia",
]
LAST_NAMES = [
    "Aguirre",
    "Peña",
    "Ríos",
    "Castaño",
    "Torres",
    "Cárdenas",
    "Mora",
    "Quintero",
    "Salcedo",
    "Rojas",
    "Beltrán",
    "Gil",
    "Moreno",
    "Villamil",
    "Ospina",
    "Bedoya",
    "Zapata",
    "Cifuentes",
    "Lozano",
    "Serrano",
]
COMPANY_NAMES = [
    "Transportes La Sabana S.A.S.",
    "Cooperativa de Vigilancia Andina",
    "Colegio San Bernardo",
    "Confecciones El Roble Ltda.",
    "Servicios Integrales del Sur S.A.S.",
]

#: Categories whose items carry lots and expiry. A shampoo does not, and S3
#: reads these two switches to decide what a movement means.
LOT_TRACKED = {
    "Analgésicos",
    "Antibióticos",
    "Cardiovascular",
    "Digestivo",
    "Respiratorio",
    "Antialérgicos",
    "Metabólico",
    "Bebidas y sueros",
}

#: A base price per category, in pesos, before the per-item spread. Roughly what
#: these things cost in a Bogotá droguería, because a catalog whose every figure
#: is `$10.000` reads as generated the moment somebody scrolls.
BASE_PRICE = {
    "Analgésicos": 9_000,
    "Antibióticos": 22_000,
    "Cardiovascular": 18_000,
    "Digestivo": 16_000,
    "Respiratorio": 20_000,
    "Antialérgicos": 14_000,
    "Metabólico": 19_000,
    "Cuidado personal": 15_000,
    "Bebidas y sueros": 6_000,
    "Dispositivos médicos": 38_000,
}


# ---------------------------------------------------------------------------
# The plan: every row this fixture would write, computed without touching the
# database, so that `owned_ids` and `build` cannot disagree about what it owns.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def item_plan(profile):
    """The catalog, as a list of plain dicts, in a fixed order."""
    shape = PROFILES[profile]
    rows = []

    if shape["token_catalog"]:
        for index, source in enumerate(vocab.generated_products()[:12]):
            rows.append(_product(source, index, minimal=True))
        return rows

    for index, drawn in enumerate(vocab.DRAWN):
        name, presentation, lab, category, price, unit, vat = drawn
        rows.append(
            _product(
                {
                    "name": name,
                    "presentation": presentation,
                    "category": category,
                    "active_ingredient": name.split(" ")[0],
                    "strength": _drawn_strength(name),
                    "unit": unit,
                    "pack_unit": unit,
                    "pack": _drawn_pack(name),
                    "drawn": True,
                    "vat_class": vat,
                    "requires_prescription": category
                    in {"Antibióticos", "Cardiovascular", "Metabólico"},
                    "controlled": False,
                    "cold_chain": False,
                    "registrable": category != "Bebidas y sueros",
                },
                index,
                manufacturer=lab,
                price=price,
            )
        )

    drawn_keys = {(row["name"], row["presentation"]) for row in rows}
    wanted = CATALOG_SIZE - len(vocab.DRAWN) - len(vocab.SERVICES)
    taken = 0
    for source in vocab.generated_products():
        if taken >= wanted:
            break
        if (source["name"], source["presentation"]) in drawn_keys:
            continue
        rows.append(_product(source, len(rows)))
        taken += 1
    if taken < wanted:
        raise RuntimeError(
            f"the catalog grammar produced {taken} products and the fixture "
            f"needs {wanted}. Widen core.catalog.vocabulary rather than "
            f"lowering CATALOG_SIZE: every check in this stage asserts "
            f"{CATALOG_SIZE}."
        )

    for index, (name, unit, price, vat, cost, barcoded) in enumerate(vocab.SERVICES):
        rows.append(
            {
                "type": ItemType.SERVICE,
                "name": name,
                "presentation": "",
                "category": "Servicios",
                "manufacturer": None,
                "active_ingredient": "",
                "strength": "",
                "unit": unit,
                "splittable": False,
                "units_per_pack": 1,
                "vat_class": vat,
                "requires_prescription": False,
                "controlled": False,
                "cold_chain": False,
                "tracks_stock": False,
                "tracks_lots": False,
                "tracks_expiry": False,
                "invima_registration": "",
                "invima_expires_at": None,
                "invima_status": InvimaStatus.NOT_APPLICABLE,
                "price": Decimal(price),
                "fixed_price": False,
                "service_cost": Decimal(cost) if cost is not None else None,
                "external_code": f"LEG-S{index:03d}",
                # One service carries a printed barcode, so the scan path over a
                # service is demonstrable: the till's lookup does not know the
                # difference and should not have to (A7).
                "barcodes": 1 if barcoded else 0,
                "supplier_links": 0,
            }
        )
    return rows


def _drawn_pack(name):
    """`Acetaminofén 500 mg × 100` holds a hundred. The drawn rows are sold as
    boxes, so the count only reaches the price ladder -- but a box of 100 that
    priced like a box of 10 would be the first figure anyone questioned."""
    tail = name.rsplit("×", 1)[-1].strip() if "×" in name else ""
    return int(tail) if tail.isdigit() else 1


def _drawn_strength(name):
    """`Losartán 50 mg × 30` is 50 mg of Losartán.

    The strength is half the key `_price_for` builds its molecule ladder from,
    so a drawn row whose strength were blank would sit on a different ladder
    than the generated rows of the same product -- and its price would
    contradict its own family on the screen the client was shown.
    """
    match = re.search(r"(\d+(?:[.,]\d+)?\s*(?:mg|mcg|g|ml|UI))\b", name)
    return match.group(1) if match else ""


def _product(source, index, *, manufacturer=None, price=None, minimal=False):
    """One product row of the plan, every field derived from its own name."""
    name = source["name"]
    seed = vocab.stable_int("item", name, source["presentation"])
    category = source["category"]

    if minimal:
        manufacturer = MINIMAL_MANUFACTURER
        category = "Medicamentos"
    elif manufacturer is None:
        # The laboratorio follows the **molecule**, not the box: a droguería
        # stocks one line of Acarbosa from one laboratorio and a second line
        # from another, and a page where every pack size of one product names a
        # different laboratorio is the tell that the catalog was generated.
        family = vocab.stable_int("lab", source["active_ingredient"] or name)
        lab = family % len(vocab.MANUFACTURERS)
        if seed % 4 == 0:
            lab = (lab + 3) % len(vocab.MANUFACTURERS)
        manufacturer = vocab.MANUFACTURERS[lab][0]

    pack = source.get("pack", 1)
    # A minority is fraccionable, which is what makes the base-unit rule visible
    # on the first screen rather than only in a check: for those the base unit
    # is one tableta and `units_per_pack` is what a box holds.
    #
    # **Never a drawn row.** The Mostrador ticket charges `1 × $2.600` for a box
    # of ten, so those fifteen products are boxes the customer walks out with;
    # a drawn row that came out fraccionable would divide its own drawn price.
    splittable = (
        not minimal
        and not source.get("drawn")
        and pack > 1
        and source["pack_unit"] != "unidad"
        and seed % 100 < 22
    )
    unit = source["pack_unit"] if splittable else source["unit"]
    units_per_pack = pack if splittable else 1

    fixed = price is not None
    box_price = price if price is not None else _price_for(source, seed)
    unit_price = (
        (Decimal(box_price) / units_per_pack).quantize(Decimal("0.01"))
        if splittable
        else Decimal(box_price)
    )

    registration, expires_at, status = _registration(name, seed, source["registrable"])
    tracked = category in LOT_TRACKED
    return {
        "type": ItemType.PRODUCT,
        "name": name,
        "presentation": source["presentation"],
        "category": category,
        "manufacturer": manufacturer,
        "active_ingredient": source["active_ingredient"],
        "strength": source["strength"],
        "unit": unit,
        "splittable": splittable,
        "units_per_pack": units_per_pack,
        "vat_class": source["vat_class"],
        "requires_prescription": source["requires_prescription"],
        "controlled": source["controlled"],
        "cold_chain": source["cold_chain"],
        "tracks_stock": True,
        "tracks_lots": tracked,
        "tracks_expiry": tracked,
        "invima_registration": registration,
        "invima_expires_at": expires_at,
        "invima_status": status,
        "price": unit_price,
        # A figure a screen draws. The Mostrador ticket totals `$15.600`, so
        # these four references are the ones no repricing, no sede override and
        # no future-dated row may move: what those rows exist to demonstrate is
        # a price *history*, and any reference in four thousand can carry one.
        "fixed_price": fixed,
        "service_cost": None,
        "external_code": f"LEG-{index:05d}",
        # A couple of hundred items carry a second code and a third, so "several
        # codes per item is normal" is visible rather than asserted. `minimal`
        # takes one of each: a token catalog is there to be counted, and a
        # second code on it would only be a second row to explain.
        "barcodes": (
            1 if minimal else 3 if seed % 53 == 0 else 2 if seed % 21 == 0 else 1
        ),
        "supplier_links": 1 if minimal else 2 if seed % 3 == 0 else 1,
    }


def _price_for(source, seed):
    """A plausible shelf price for one box.

    Three factors, in the order that makes the column read as a real price list:
    the category's own level, the **molecule's** own level -- so every pack size
    of one product sits on one price ladder rather than scattering -- and the
    pack, sub-linearly, because a box of 100 costs less per tablet than a box of
    10. A ±4% jitter per reference keeps the column from repeating a figure.

    What this replaces is the failure worth naming: a per-item spread makes
    `× 50` cost less than `× 20` of the same molecule, and a catalog whose price
    ladder is incoherent is one nobody believes and one every later margin
    figure inherits.
    """
    category = source["category"]
    pack = source.get("pack", 1)
    base = BASE_PRICE.get(category, 12_000)
    molecule = vocab.stable_int(
        "molecule", source["active_ingredient"] or source["name"], source["strength"]
    )
    level = (45 + molecule % 260) / 100
    size = (pack / 30) ** 0.82 if pack > 1 else 1.0
    jitter = (96 + seed % 9) / 100
    return int(round(base * level * size * jitter / 50)) * 50 or 50


def _registration(name, seed, registrable):
    """A plausible registro INVIMA, spread across all four states.

    A dozen expire inside 90 days and a handful are already past, so the badge,
    the `Vence` filter and the nightly sweep each have rows to act on the moment
    the tenant exists. A `valid` row is never given a past date: the sweep would
    move it on its first run and the seed would stop being idempotent.
    """
    if not registrable:
        return "", None, InvimaStatus.NOT_APPLICABLE
    today = timezone.localdate()
    bucket = seed % 1000
    number = f"INVIMA {2016 + seed % 9}M-{seed % 10_000_000:07d}"
    if bucket < 5:
        return number, today - timedelta(days=30 + seed % 600), InvimaStatus.EXPIRED
    if bucket < 45:
        # `in_process` may sit past its date and the sweep does not touch it:
        # INVIMA has the file, and whether the product still sells is the
        # pharmacy's call rather than Botica's.
        return number, today - timedelta(days=seed % 120), InvimaStatus.IN_PROCESS
    # `not_applicable` is not a share of the registrable rows: it means the row
    # is not a registrable product at all -- every service and the non-medicinal
    # lines a droguería sells -- and it is decided by `registrable` above. A
    # medicamento reading **No aplica** is the tell that a seed sprayed the enum
    # across rows rather than deriving it.
    if (seed // 1000) % 250 == 0:
        return number, today + timedelta(days=8 + seed % 80), InvimaStatus.VALID
    return number, today + timedelta(days=200 + seed % 1600), InvimaStatus.VALID


@lru_cache(maxsize=8)
def customer_plan(profile):
    """The clientes, with the three reserved rows first so they are stable."""
    count = PROFILES[profile]["customers"]
    # `minimal` builds none of them: it exists to be the tenant another tenant
    # is isolated *from*, the Ley 1581 check runs on `default`, and two reserved
    # rows nobody reads are two rows that would have to stay reserved forever.
    rows: list[dict] = (
        []
        if not PROFILES[profile]["reserved_customers"]
        else [
            {
                "key": f"{RESERVED_NEVER_REFERENCED[0]}:{RESERVED_NEVER_REFERENCED[1]}",
                "document_type": RESERVED_NEVER_REFERENCED[0],
                "document": RESERVED_NEVER_REFERENCED[1],
                "name": "Hernando Villamil Ruiz",
                "consent": True,
                "note": "Reservado: ninguna otra fixture puede referenciarlo.",
            },
            {
                "key": f"{RESERVED_FOR_SALES[0]}:{RESERVED_FOR_SALES[1]}",
                "document_type": RESERVED_FOR_SALES[0],
                "document": RESERVED_FOR_SALES[1],
                "name": "Marta Lucía Ospina",
                "consent": True,
                "note": "Reservado para las ventas de la fixture de S4.",
            },
            {
                "key": RESERVED_ERASED,
                "document_type": "",
                "document": "",
                "name": "",
                "consent": False,
                "note": "",
            },
        ]
    )
    for index in range(count - len(rows)):
        # Not keyed on the profile: a document number belongs to one person,
        # and `demo-la-45` and `demo-la-45-young` are the same network with a
        # shorter history. The ids still differ, because `SeedContext.uid`
        # prefixes the profile itself.
        seed = vocab.stable_int("customer", index)
        document_type = DOCUMENT_TYPES_IN_SEED[index % len(DOCUMENT_TYPES_IN_SEED)]
        if document_type == "NIT":
            name = COMPANY_NAMES[seed % len(COMPANY_NAMES)]
            document = f"9993{index:05d}"
        else:
            name = (
                f"{FIRST_NAMES[seed % len(FIRST_NAMES)]} "
                f"{LAST_NAMES[(seed // 7) % len(LAST_NAMES)]}"
            )
            document = f"9001{index:05d}"
        rows.append(
            {
                "key": f"{document_type}:{document}",
                "document_type": document_type,
                "document": document,
                "name": name,
                "consent": seed % 3 != 0,
                "note": "",
            }
        )
    return rows


def import_key(profile):
    return f"demo_seed:{profile}"


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------


def owned_ids(context):
    """Exactly the rows this fixture writes in its guard tables, so the command
    can refuse a tenant holding any other."""
    profile = context.profile
    return {
        "items": {context.uid("items", _item_key(row)) for row in item_plan(profile)},
        "customers": {
            context.uid("customers", row["key"]) for row in customer_plan(profile)
        },
        "imports": {context.uid("imports", import_key(profile))},
    }


def _item_key(row):
    return f"{row['name']}|{row['presentation']}"


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


def build(context):
    """Write the catalog, inside the pin the command already opened."""
    profile = context.profile
    shape = PROFILES[profile]

    manufacturers = _write_manufacturers(context, shape)
    categories = _write_categories(context, shape)
    suppliers = _write_suppliers(context, shape)
    items = _write_items(context, manufacturers, categories)
    _write_barcodes(context, items)
    _write_supplier_items(context, items, suppliers)
    _write_prices(context, items, shape)
    _write_customers(context)
    _write_import_row(context)


def _existing(model, tenant_id):
    return set(model.objects.filter(tenant_id=tenant_id).values_list("id", flat=True))


def _insert(context, model, rows, table):
    """Create only the rows that are not there, so a second run genuinely
    changes nothing -- `updated_at` included."""
    held = _existing(model, context.tenant_id)
    missing = [row for row in rows if row.id not in held]
    if missing:
        model.objects.bulk_create(missing, batch_size=1000)
    context.wrote(table, len(rows))
    return {row.id: row for row in rows}


def _write_manufacturers(context, shape):
    entries = (
        [(MINIMAL_MANUFACTURER, "999.100.099-9")]
        if shape["token_catalog"]
        else vocab.MANUFACTURERS
    )
    rows = [
        Manufacturer(
            id=context.uid("manufacturers", name),
            tenant_id=context.tenant_id,
            name=name,
            nit=nit,
        )
        for name, nit in entries
    ]
    _insert(context, Manufacturer, rows, "manufacturers")
    return {row.name: row for row in rows}


def _write_categories(context, shape):
    tree = [("Medicamentos", [])] if shape["token_catalog"] else vocab.CATEGORIES
    parents = [
        Category(
            id=context.uid("categories", name),
            tenant_id=context.tenant_id,
            name=name,
            parent_id=None,
        )
        for name, _children in tree
    ]
    by_name = {row.name: row for row in parents}
    children = []
    for name, child_names in tree:
        for child in child_names:
            children.append(
                Category(
                    id=context.uid("categories", f"{name}>{child}"),
                    tenant_id=context.tenant_id,
                    name=child,
                    parent_id=by_name[name].id,
                )
            )
    # Parents first: the two-level trigger reads the parent row to refuse a
    # third level, and a child inserted before its parent names nothing.
    _insert(context, Category, parents + children, "categories")
    by_name.update({row.name: row for row in children})
    return by_name


def _write_suppliers(context, shape):
    entries = [vocab.SUPPLIERS[0]] if shape["token_catalog"] else vocab.SUPPLIERS
    rows = [
        Supplier(
            id=context.uid("suppliers", nit),
            tenant_id=context.tenant_id,
            nit=nit,
            name=name,
            contact=f"{contact} · {DEMO_PHONE}",
            payment_terms=terms,
            lead_time_days=lead,
        )
        for nit, name, contact, terms, lead in entries
    ]
    _insert(context, Supplier, rows, "suppliers")
    return rows


def _write_items(context, manufacturers, categories):
    plan = item_plan(context.profile)
    rows = []
    for entry in plan:
        rows.append(
            Item(
                id=context.uid("items", _item_key(entry)),
                tenant_id=context.tenant_id,
                type=entry["type"],
                name=entry["name"],
                presentation=entry["presentation"],
                description="",
                manufacturer_id=(
                    manufacturers[entry["manufacturer"]].id
                    if entry["manufacturer"]
                    else None
                ),
                category_id=categories[entry["category"]].id,
                active_ingredient=entry["active_ingredient"],
                strength=entry["strength"],
                invima_registration=entry["invima_registration"],
                invima_expires_at=entry["invima_expires_at"],
                invima_status=entry["invima_status"],
                requires_prescription=entry["requires_prescription"],
                controlled=entry["controlled"],
                cold_chain=entry["cold_chain"],
                unit=entry["unit"],
                splittable=entry["splittable"],
                units_per_pack=entry["units_per_pack"],
                vat_class=entry["vat_class"],
                tracks_stock=entry["tracks_stock"],
                tracks_lots=entry["tracks_lots"],
                tracks_expiry=entry["tracks_expiry"],
                active=True,
                custom={},
                external_code=entry["external_code"],
                service_cost=entry["service_cost"],
            )
        )
    _insert(context, Item, rows, "items")
    return list(zip(plan, rows, strict=True))


def _write_barcodes(context, items):
    rows = []
    for entry, item in items:
        seed = vocab.stable_int("barcode", entry["name"], entry["presentation"])
        for ordinal in range(entry["barcodes"]):
            code = vocab.ean13(seed + ordinal * 7919)
            rows.append(
                ItemBarcode(
                    id=context.uid("item_barcodes", code),
                    tenant_id=context.tenant_id,
                    item_id=item.id,
                    code=code,
                    is_primary=ordinal == 0,
                )
            )
    _insert(context, ItemBarcode, rows, "item_barcodes")


def _write_supplier_items(context, items, suppliers):
    """Exactly one `is_preferred` per item, and a `cost` below the price
    everywhere, so no margin figure on any screen is negative."""
    rows = []
    for entry, item in items:
        if entry["supplier_links"] == 0:
            continue
        seed = vocab.stable_int("supplier_item", entry["name"], entry["presentation"])
        box_price = Decimal(entry["price"]) * item.units_per_pack
        for ordinal in range(min(entry["supplier_links"], len(suppliers))):
            supplier = suppliers[(seed + ordinal) % len(suppliers)]
            factor = Decimal(62 + (seed // (ordinal + 1)) % 17) / Decimal(100)
            rows.append(
                SupplierItem(
                    id=context.uid(
                        "supplier_items", f"{supplier.nit}|{_item_key(entry)}"
                    ),
                    tenant_id=context.tenant_id,
                    supplier_id=supplier.id,
                    item_id=item.id,
                    supplier_code=f"{supplier.nit[-4:]}-{seed % 100_000:05d}",
                    cost=(box_price * factor).quantize(Decimal("0.01")),
                    min_order_pack=1 if ordinal else (1 + seed % 4),
                    is_preferred=ordinal == 0,
                )
            )
    # Two links can land on the same supplier for the same item, and the pair is
    # unique by constraint. Keeping the first is what the second would have
    # meant anyway: one preferred link and nothing else to say.
    unique: dict = {}
    for row in rows:
        unique.setdefault(row.id, row)
    _insert(context, SupplierItem, list(unique.values()), "supplier_items")


def _write_prices(context, items, shape):
    """One open network-wide row per item, plus the history the profile asks for.

    **Every seeded opening price carries `source = 'imported'`**, with
    `proposal_id` and `set_by_user_id` null: it arrived from a file and no person
    typed it. The exception is deliberate -- the references whose price moves
    mid-window are `manual` with the seeded administrator's id, because a
    repricing is a person's act and a demo that showed no name in the price
    history would be showing a column that never fills.

    **`proposal_id` is null on every seeded row without exception**, because
    nothing in the seed produces a suggestion and S7's fixture is the stage that
    will. The enum admits nothing further, because there is no third value to
    admit (A11).
    """
    today = timezone.localdate()
    window = shape["window_days"]
    opened_on = today - timedelta(days=window + 30 if window else 90)
    pricer = _pricer(context)
    locations = list(
        Location.objects.filter(tenant_id=context.tenant_id).order_by("code")
    )

    # Only references whose figure no screen draws.
    movable = [pair for pair in items if not pair[0]["fixed_price"]]
    repriced = {_item_key(entry) for entry, _item in movable[: shape["repriced"]]}
    future_key = _item_key(movable[0][0]) if shape["future_price"] and movable else None

    rows = []
    for entry, item in items:
        key = _item_key(entry)
        opening = Decimal(entry["price"])
        moves = []
        if key in repriced:
            seed = vocab.stable_int("reprice", key)
            steps = 2 + seed % 2
            for step in range(1, steps + 1):
                day = today - timedelta(days=max(1, window * (steps - step + 1) // 4))
                factor = Decimal(104 + (seed // (step + 1)) % 13) / Decimal(100)
                moves.append((day, _shelf(opening * factor)))
        if key == future_key:
            moves.append((today + timedelta(days=7), _shelf(opening * Decimal("1.05"))))

        # The opening row closes when the first repricing starts; each move
        # closes when the next one does. One open row per scope, always.
        boundaries = [day for day, _price in moves]
        rows.append(
            ItemPrice(
                id=context.uid("item_prices", f"{key}|open"),
                tenant_id=context.tenant_id,
                item_id=item.id,
                location_id=None,
                price=opening,
                effective_from=opened_on,
                effective_to=boundaries[0] if boundaries else None,
                source=PriceSource.IMPORTED,
                proposal_id=None,
                set_by_user_id=None,
            )
        )
        for index, (day, price) in enumerate(moves):
            rows.append(
                ItemPrice(
                    id=context.uid("item_prices", f"{key}|move{index}"),
                    tenant_id=context.tenant_id,
                    item_id=item.id,
                    location_id=None,
                    price=price,
                    effective_from=day,
                    effective_to=(
                        boundaries[index + 1] if index + 1 < len(boundaries) else None
                    ),
                    source=PriceSource.MANUAL,
                    proposal_id=None,
                    set_by_user_id=pricer.id if pricer else None,
                    set_by_name=pricer.name if pricer else "",
                )
            )

    # A handful scoped to a sede -- Chapinero where Chapinero exists, and
    # whatever sedes the profile built where it does not. Under `scale` that is
    # twenty rather than six, which is the only thing a profile changes the size
    # of in this stage.
    per_location = shape["overrides_per_location"]
    if per_location and locations and movable:
        for index, location in enumerate(locations):
            for ordinal in range(per_location):
                entry, item = movable[(index * per_location + ordinal) % len(movable)]
                key = _item_key(entry)
                factor = Decimal(96 + (index + ordinal) % 9) / Decimal(100)
                rows.append(
                    ItemPrice(
                        id=context.uid("item_prices", f"{key}|{location.code}"),
                        tenant_id=context.tenant_id,
                        item_id=item.id,
                        location_id=location.id,
                        price=_shelf(Decimal(entry["price"]) * factor),
                        effective_from=today - timedelta(days=30),
                        effective_to=None,
                        source=PriceSource.MANUAL,
                        proposal_id=None,
                        set_by_user_id=pricer.id if pricer else None,
                        set_by_name=pricer.name if pricer else "",
                    )
                )

    unique: dict = {}
    for row in rows:
        unique.setdefault(row.id, row)
    _insert(context, ItemPrice, list(unique.values()), "item_prices")


def _shelf(amount):
    """A repricing lands on a figure somebody would actually put on a shelf.

    A demo whose price history reads `$17.574` is a demo whose prices were
    computed rather than decided, and that is the first thing anyone notices.
    Whole-peso items round to the nearest fifty; a fraccionable base unit keeps
    its centavos, because a tablet at $416,67 is a real figure and rounding it
    would break the box arithmetic §A.11 depends on.
    """
    amount = Decimal(amount)
    if amount < 1000:
        return amount.quantize(Decimal("0.01"))
    return (amount / 50).quantize(Decimal("1")) * 50


def _pricer(context):
    """Whichever seeded user holds price rights -- the administrator wherever
    there is one, the single `owner` under `minimal`.

    A fixture that hard-coded three users would fail on two profiles out of
    five, and it would fail late, inside somebody else's stage, on a run they
    did not change.
    """
    people = list(User.objects.filter(tenant_id=context.tenant_id).order_by("email"))
    for role in (Role.ADMIN, Role.OWNER):
        for person in people:
            if person.role == role:
                return person
    return people[0] if people else None


def _write_customers(context):
    rows = []
    for entry in customer_plan(context.profile):
        consent = entry["consent"]
        rows.append(
            Customer(
                id=context.uid("customers", entry["key"]),
                tenant_id=context.tenant_id,
                document_type=entry["document_type"],
                document=entry["document"],
                name=entry["name"],
                phone=DEMO_PHONE if entry["name"] else "",
                email=(
                    f"{entry['document']}@{DEMO_EMAIL_DOMAIN}"
                    if entry["name"] and entry["document_type"] != "NIT"
                    else ""
                ),
                address="Calle 63 # 13-45" if entry["name"] else "",
                data_consent=consent,
                data_consent_at=timezone.now() if consent else None,
                notes=entry["note"],
            )
        )
    _insert(context, Customer, rows, "customers")


def _write_import_row(context):
    """Invariant 1's marker, and the record of the run.

    It is **this fixture's to write rather than the command's**, because
    `imports` is S1's table and does not exist at S0 -- `imports.kind` being text
    rather than a Postgres enum is what lets it exist without a migration.
    """
    row_id = context.uid("imports", import_key(context.profile))
    if ImportRun.objects.filter(id=row_id).exists():
        context.wrote("imports", 1)
        return
    ImportRun.objects.create(
        id=row_id,
        tenant_id=context.tenant_id,
        kind=ImportKind.DEMO_SEED,
        source=f"seed_demo_tenant --profile {context.profile}",
        status=ImportStatus.COMPLETED,
        dry_run=False,
        started_at=timezone.now(),
        finished_at=timezone.now(),
        rows_read=0,
        rows_created=0,
        rows_updated=0,
        rows_failed=0,
        errors=[
            {
                "file": "catalog",
                "line": 0,
                "value": context.profile,
                "reason": (
                    "datos sintéticos: manufacturers, categories, suppliers, "
                    "items, item_barcodes, supplier_items, item_prices, customers"
                ),
            }
        ],
        started_by_user=None,
    )
    context.wrote("imports", 1)


register(
    "catalog",
    tables=("items", "customers", "imports"),
    requires=("identity",),
    build=build,
    owned_ids=owned_ids,
)
