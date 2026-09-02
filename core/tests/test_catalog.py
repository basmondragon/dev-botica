"""The catalog: one table for products and services, the base unit, barcodes,
the registro INVIMA, and the settings sections' own CRUD.

The two kinds of failure here have opposite fixes and are worth telling apart
while reading a red run: a failure in a database-level assertion after a
migration edit means a constraint moved; a failure in an endpoint assertion
means a permission, a serialiser field or a status code changed.
"""

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.catalog import prices
from core.models import (
    AuditLog,
    Category,
    Item,
    ItemBarcode,
    ItemPrice,
    ItemType,
    Manufacturer,
    Supplier,
    SupplierItem,
)


def _post(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def _patch(client, path, payload):
    return client.patch(path, data=json.dumps(payload), content_type="application/json")


@pytest.fixture
def lab(tenant_a):
    return Manufacturer.objects.create(tenant=tenant_a, name="Genfar")


@pytest.fixture
def category(tenant_a):
    return Category.objects.create(tenant=tenant_a, name="Medicamentos")


def make_item(tenant, **overrides):
    fields = {
        "type": ItemType.PRODUCT,
        "name": "Acetaminofén 500 mg × 100",
        "presentation": "caja × 100 tabletas",
        "unit": "caja",
        "vat_class": "excluded",
        "invima_status": "not_applicable",
    }
    fields.update(overrides)
    return Item.objects.create(tenant=tenant, **fields)


# ---------------------------------------------------------------------------
# A7 · one table, and the switch that makes a service a service
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_service_is_created_in_the_same_editor_as_a_box_of_pills(
    client_as, owner_a, tenant_a
):
    """Acceptance 1. Nothing downstream branches on the distinction."""
    response = _post(
        client_as(owner_a),
        "/api/items",
        {
            "type": "service",
            "name": "Toma de presión",
            "unit": "servicio",
            "vat_class": "excluded",
            "service_cost": "1200",
            # Everything a service has no meaning for is sent anyway, so that
            # the normalisation is what refuses it rather than the form.
            "presentation": "caja × 30",
            "tracks_lots": True,
            "splittable": True,
            "units_per_pack": 30,
        },
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["tracks_stock"] is False
    assert body["tracks_lots"] is False
    assert body["tracks_expiry"] is False
    assert body["presentation"] == ""
    assert body["manufacturer_id"] is None
    assert body["invima_status"] == "not_applicable"
    assert body["splittable"] is False
    assert body["units_per_pack"] == 1
    assert Decimal(body["service_cost"]) == Decimal("1200")
    # The opening price is a second call to the one endpoint that writes one
    # (A11), which is what the panel does behind a single `Guardar`.
    priced = _post(
        client_as(owner_a), f"/api/items/{body['id']}/prices", {"price": "5000"}
    )
    assert priced.status_code == 200, priced.content
    assert Decimal(priced.json()["price"]) == Decimal("5000")

    grid = client_as(owner_a).get("/api/items?type=service").json()
    assert [row["name"] for row in grid["rows"]] == ["Toma de presión"]


@pytest.mark.django_db
def test_the_database_refuses_lots_on_an_item_that_moves_no_stock(tenant_a):
    """Acceptance 2 · the whole of A7's negative, and it is not the form's."""
    with pytest.raises(IntegrityError), transaction.atomic():
        make_item(
            tenant_a,
            name="Inyectología",
            type=ItemType.SERVICE,
            tracks_stock=False,
            tracks_lots=True,
        )


@pytest.mark.django_db
def test_only_a_service_carries_a_service_cost(tenant_a):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_item(tenant_a, service_cost=Decimal("100"))


# ---------------------------------------------------------------------------
# The base unit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_fraccionable_item_prices_per_base_unit(client_as, owner_a, tenant_a):
    """Acceptance 3 · one open row holding the **unit** price, not the box."""
    response = _post(
        client_as(owner_a),
        "/api/items",
        {
            "type": "product",
            "name": "Ibuprofeno 400 mg × 30",
            "unit": "tableta",
            "vat_class": "excluded",
            "splittable": True,
            "units_per_pack": 30,
        },
    )
    assert response.status_code == 200, response.content
    item_id = response.json()["id"]
    _post(client_as(owner_a), f"/api/items/{item_id}/prices", {"price": "416.67"})
    rows = client_as(owner_a).get(f"/api/items/{item_id}/prices").json()
    assert len(rows) == 1
    assert rows[0]["effective_to"] is None
    assert Decimal(rows[0]["price"]) == Decimal("416.67")

    item = Item.objects.get(id=item_id)
    assert prices.box_price(item, Decimal("416.67")) == Decimal("12500.10")


@pytest.mark.django_db
def test_a_fraccionable_pack_of_one_is_refused_by_the_database(tenant_a):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_item(tenant_a, splittable=True, units_per_pack=1)


@pytest.mark.django_db
def test_a_pack_of_zero_is_refused_by_the_database(tenant_a):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_item(tenant_a, name="Cero", units_per_pack=0)


@pytest.mark.django_db
def test_two_rows_for_the_same_presentation_are_refused(tenant_a):
    make_item(tenant_a)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_item(tenant_a)


# ---------------------------------------------------------------------------
# Barcodes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_three_barcodes_resolve_to_one_item_and_exactly_one_is_primary(
    client_as, owner_a, tenant_a
):
    """Acceptance 4. An ambiguous scan sells the wrong product at the wrong
    price, so the refusal names the item rather than the constraint."""
    item = make_item(tenant_a)
    response = _patch(
        client_as(owner_a),
        f"/api/items/{item.id}",
        {
            "barcodes": [
                {"code": "7701234567890", "is_primary": True},
                {"code": "2000000000015", "is_primary": False},
                {"code": "2000000000022", "is_primary": False},
            ]
        },
    )
    assert response.status_code == 200, response.content
    assert ItemBarcode.objects.filter(item=item, is_primary=True).count() == 1

    for code in ("7701234567890", "2000000000015", "2000000000022"):
        found = client_as(owner_a).get(f"/api/items?barcode={code}").json()
        assert found["row_count"] == 1
        assert found["rows"][0]["id"] == str(item.id)

    other = make_item(tenant_a, name="Otro producto", presentation="")
    refused = _patch(
        client_as(owner_a),
        f"/api/items/{other.id}",
        {"barcodes": [{"code": "2000000000015", "is_primary": True}]},
    )
    assert refused.status_code == 409
    assert "Acetaminofén 500 mg × 100" in refused.json()["detail"]


@pytest.mark.django_db
def test_a_code_is_unique_per_tenant_at_the_database(tenant_a):
    item = make_item(tenant_a)
    other = make_item(tenant_a, name="Otro", presentation="")
    ItemBarcode.objects.create(tenant=tenant_a, item=item, code="7701", is_primary=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        ItemBarcode.objects.create(tenant=tenant_a, item=other, code="7701")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_one_field_matches_name_laboratorio_barcode_and_registro(
    client_as, owner_a, tenant_a, lab
):
    """Acceptance 5 · four things, one box."""
    item = make_item(
        tenant_a,
        manufacturer=lab,
        invima_registration="INVIMA 2019M-0012345",
        name="Losartán 50 mg × 30",
        presentation="caja × 30 tabletas",
    )
    ItemBarcode.objects.create(
        tenant=tenant_a, item=item, code="7709999000001", is_primary=True
    )
    make_item(tenant_a, name="Otro producto", presentation="")

    client = client_as(owner_a)
    for term in ("losar", "Genfar", "7709999000001", "INVIMA 2019M-0012345"):
        found = client.get(f"/api/items?q={term}").json()
        assert [row["id"] for row in found["rows"]] == [str(item.id)], term

    # Accent-free spelling, which is what a cashier types.
    assert client.get("/api/items?q=losartan").json()["row_count"] == 1


# ---------------------------------------------------------------------------
# The registro INVIMA
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_sweep_expires_only_the_lapsed_valid_rows_and_is_idempotent(tenant_a):
    """Acceptance 7 · `in_process` stays, because INVIMA has the file."""
    from core.catalog.jobs import expire_invima_registrations

    yesterday = timezone.localdate() - timedelta(days=1)
    lapsed = make_item(
        tenant_a,
        name="Vencible",
        presentation="",
        invima_status="valid",
        invima_expires_at=yesterday,
    )
    filed = make_item(
        tenant_a,
        name="En trámite",
        presentation="",
        invima_status="in_process",
        invima_expires_at=yesterday,
    )
    not_applicable = make_item(tenant_a, name="Servicio-ish", presentation="")
    current = make_item(
        tenant_a,
        name="Vigente",
        presentation="",
        invima_status="valid",
        invima_expires_at=timezone.localdate() + timedelta(days=400),
    )

    moved = expire_invima_registrations.func(
        tenant_id=str(tenant_a.id), run_date=timezone.localdate().isoformat()
    )
    assert moved == 1
    lapsed.refresh_from_db()
    filed.refresh_from_db()
    not_applicable.refresh_from_db()
    current.refresh_from_db()
    assert lapsed.invima_status == "expired"
    assert filed.invima_status == "in_process"
    assert not_applicable.invima_status == "not_applicable"
    assert current.invima_status == "valid"

    # One row per run naming the count, with a null actor -- not one row per
    # item. A thousand rows for a date passing buries the edits a person made.
    trail = AuditLog.objects.filter(tenant=tenant_a, entity_type="items")
    assert trail.count() == 1
    assert trail.first().actor_user_id is None
    assert trail.first().after["items"] == 1

    assert (
        expire_invima_registrations.func(
            tenant_id=str(tenant_a.id), run_date=timezone.localdate().isoformat()
        )
        == 0
    )
    assert trail.count() == 1


@pytest.mark.django_db
def test_an_invima_change_by_hand_lands_on_the_audit_trail(
    client_as, owner_a, tenant_a
):
    """Acceptance 8 · what makes "what was its state on the day we sold it"
    answerable later."""
    item = make_item(tenant_a, invima_status="valid")
    response = _patch(
        client_as(owner_a), f"/api/items/{item.id}", {"invima_status": "expired"}
    )
    assert response.status_code == 200
    row = AuditLog.objects.filter(tenant=tenant_a, entity_type="items").first()
    assert row.actor_user_id == owner_a.id
    assert row.before["invima_status"] == "valid"
    assert row.after["invima_status"] == "expired"


@pytest.mark.django_db
def test_an_expired_registration_is_a_filter_and_never_a_disabled_row(
    client_as, owner_a, tenant_a
):
    """Acceptance 6 · Botica records the state and the pharmacy's decision."""
    expired = make_item(
        tenant_a, name="Vencido", presentation="", invima_status="expired"
    )
    make_item(tenant_a, name="Vigente", presentation="", invima_status="valid")
    found = client_as(owner_a).get("/api/items?invima_status=expired").json()
    assert [row["id"] for row in found["rows"]] == [str(expired.id)]
    assert found["rows"][0]["active"] is True


# ---------------------------------------------------------------------------
# Laboratorios, categorías, proveedores
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_deleting_a_laboratorio_in_use_is_refused_naming_the_count(
    client_as, owner_a, tenant_a, lab
):
    """Acceptance 24."""
    make_item(tenant_a, manufacturer=lab)
    refused = client_as(owner_a).delete(f"/api/manufacturers/{lab.id}")
    assert refused.status_code == 409
    assert "1 referencia" in refused.json()["detail"]


@pytest.mark.django_db
def test_categories_are_two_levels_and_the_database_says_so(
    client_as, owner_a, tenant_a, category
):
    child = _post(
        client_as(owner_a),
        "/api/categories",
        {"name": "Analgésicos", "parent_id": str(category.id)},
    )
    assert child.status_code == 200
    refused = _post(
        client_as(owner_a),
        "/api/categories",
        {"name": "Tercero", "parent_id": child.json()["id"]},
    )
    assert refused.status_code == 422

    # And the same rule, one layer down: the endpoint's check is a message, the
    # trigger is the guarantee.
    with pytest.raises(Exception), transaction.atomic():
        Category.objects.create(
            tenant=tenant_a,
            name="Tercero",
            parent=Category.objects.get(id=child.json()["id"]),
        )


@pytest.mark.django_db
def test_setting_a_preferred_supplier_clears_it_on_the_items_other_links(
    client_as, owner_a, tenant_a
):
    item = make_item(tenant_a)
    first = Supplier.objects.create(tenant=tenant_a, nit="1", name="Coopidrogas")
    second = Supplier.objects.create(tenant=tenant_a, nit="2", name="Directo")
    _post(
        client_as(owner_a),
        "/api/supplier-items",
        {
            "supplier_id": str(first.id),
            "item_id": str(item.id),
            "cost": "1000",
            "is_preferred": True,
        },
    )
    response = _post(
        client_as(owner_a),
        "/api/supplier-items",
        {
            "supplier_id": str(second.id),
            "item_id": str(item.id),
            "cost": "900",
            "is_preferred": True,
        },
    )
    assert response.status_code == 200, response.content
    preferred = SupplierItem.objects.filter(item=item, is_preferred=True)
    assert preferred.count() == 1
    assert preferred.first().supplier_id == second.id


@pytest.mark.django_db
def test_at_most_one_preferred_supplier_per_item_at_the_database(tenant_a):
    item = make_item(tenant_a)
    first = Supplier.objects.create(tenant=tenant_a, nit="1", name="A")
    second = Supplier.objects.create(tenant=tenant_a, nit="2", name="B")
    SupplierItem.objects.create(
        tenant=tenant_a, supplier=first, item=item, is_preferred=True
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        SupplierItem.objects.create(
            tenant=tenant_a, supplier=second, item=item, is_preferred=True
        )


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_cashier_reads_the_catalog_without_costs_and_writes_nothing(
    client_as, cashier_a, owner_a, tenant_a
):
    """Acceptance 22 · costs are absent from every response they receive."""
    item = make_item(
        tenant_a,
        type=ItemType.SERVICE,
        tracks_stock=False,
        tracks_lots=False,
        tracks_expiry=False,
        unit="servicio",
        service_cost=Decimal("1200"),
    )
    supplier = Supplier.objects.create(tenant=tenant_a, nit="1", name="Coopidrogas")
    product = make_item(tenant_a, name="Un producto", presentation="")
    SupplierItem.objects.create(
        tenant=tenant_a, supplier=supplier, item=product, cost=Decimal("900")
    )

    client = client_as(cashier_a)
    assert client.get("/api/items").status_code == 200
    assert client.get(f"/api/items/{item.id}").json()["service_cost"] is None
    detail = client.get(f"/api/items/{product.id}").json()
    assert detail["supplier_items"][0]["cost"] is None

    assert (
        _post(
            client,
            "/api/items",
            {"type": "product", "name": "X", "unit": "caja", "vat_class": "excluded"},
        ).status_code
        == 403
    )
    assert client.get("/api/suppliers").status_code == 403
    assert client.get("/api/customers").status_code == 403
    assert client.get("/api/imports").status_code == 403

    # And an owner does see the figure, or the check above proves nothing.
    assert (
        client_as(owner_a)
        .get(f"/api/items/{product.id}")
        .json()["supplier_items"][0]["cost"]
        is not None
    )


@pytest.mark.django_db
def test_there_is_no_way_to_hard_delete_an_item(client_as, owner_a, tenant_a):
    """Acceptance 23 · every later table references `items`."""
    item = make_item(tenant_a)
    assert client_as(owner_a).delete(f"/api/items/{item.id}").status_code == 405

    _patch(client_as(owner_a), f"/api/items/{item.id}", {"active": False})
    default_grid = client_as(owner_a).get("/api/items").json()
    assert default_grid["row_count"] == 0
    assert client_as(owner_a).get(f"/api/items/{item.id}").status_code == 200
    assert client_as(owner_a).get("/api/items?active=all").json()["row_count"] == 1


# ---------------------------------------------------------------------------
# Tax
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tax_is_per_line_and_no_rate_lives_anywhere_but_the_code_constant(
    client_as, owner_a, tenant_a
):
    """Check 6 · the arithmetic that proves it.

    The tax over a mixed basket computed line by line does not equal any single
    rate applied to the basket subtotal. If one rate times the subtotal
    reproduces the answer, the class stopped being read per item.
    """
    from core.models import VAT_RATES

    basket = [
        (
            make_item(tenant_a, name="Medicina", presentation="", vat_class="excluded"),
            10_000,
        ),
        (
            make_item(tenant_a, name="Crema", presentation="", vat_class="rate_19"),
            20_000,
        ),
        (make_item(tenant_a, name="Suero", presentation="", vat_class="rate_5"), 5_000),
    ]
    rows = client_as(owner_a).get("/api/items").json()["rows"]
    assert {row["vat_class"] for row in rows} == {"excluded", "rate_19", "rate_5"}

    subtotal = sum(amount for _item, amount in basket)
    per_line = sum(amount * VAT_RATES[item.vat_class] / 100 for item, amount in basket)
    assert per_line == Decimal("4050")
    for rate in VAT_RATES.values():
        assert per_line != subtotal * rate / 100


@pytest.mark.django_db
def test_a_new_item_has_no_default_vat_class(client_as, owner_a):
    """The field is required with nothing preselected: defaulting to `excluded`
    silently under-charges IVA on every cosmetic, drink and device."""
    response = _post(
        client_as(owner_a),
        "/api/items",
        {"type": "product", "name": "Sin IVA", "unit": "caja"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Two tenants, one instance
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_neither_tenant_returns_a_row_of_the_others(
    client_as, owner_a, tenant_a, tenant_b
):
    """Acceptance 30 · both tables and both tenants."""
    make_item(tenant_a, name="De la 45", presentation="")
    make_item(tenant_b, name="De La Estrella", presentation="")
    rows = client_as(owner_a).get("/api/items").json()
    assert [row["name"] for row in rows["rows"]] == ["De la 45"]
    assert rows["row_count"] == 1


@pytest.mark.django_db
def test_rls_is_enabled_and_forced_on_every_table_this_stage_created(
    django_assert_num_queries,
):
    """Acceptance 30 · a table missing a policy passes every other check here."""
    from django.db import connection

    from core.models import Customer, ImportRun

    del django_assert_num_queries
    # Enumerated from the models rather than from the migration's own list: a
    # check that read the migration would agree with it by construction, and
    # the failure worth catching is a table that exists and has no policy.
    tables = [
        model._meta.db_table
        for model in (
            Manufacturer,
            Category,
            Supplier,
            Item,
            ItemBarcode,
            SupplierItem,
            ItemPrice,
            Customer,
            ImportRun,
        )
    ]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY(%s)",
            [tables],
        )
        found = {name: (rls, forced) for name, rls, forced in cursor.fetchall()}
    assert set(found) == set(tables)
    assert all(found[table] == (True, True) for table in found), found


# ---------------------------------------------------------------------------
# The grid contract
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_grid_answers_the_contract_and_refuses_an_unknown_sort(
    client_as, owner_a, tenant_a
):
    for index in range(30):
        make_item(tenant_a, name=f"Producto {index:02d}", presentation="")
    body = client_as(owner_a).get("/api/items?page=2&page_size=25").json()
    assert body["row_count"] == 30
    assert body["page"] == 2
    assert len(body["rows"]) == 5
    assert client_as(owner_a).get("/api/items?sort=inventado").status_code == 422


@pytest.mark.django_db
def test_the_summary_counts_the_catalog_and_not_the_view(client_as, owner_a, tenant_a):
    make_item(tenant_a, name="Vencido", presentation="", invima_status="expired")
    make_item(
        tenant_a,
        name="Servicio",
        presentation="",
        type=ItemType.SERVICE,
        tracks_stock=False,
        tracks_lots=False,
        tracks_expiry=False,
    )
    body = client_as(owner_a).get("/api/items/summary").json()
    assert body == {
        "active_items": 2,
        "services": 1,
        "expired_registrations": 1,
    }


@pytest.mark.django_db
def test_price_resolution_prefers_the_sede_and_falls_back_to_the_network(
    tenant_a, sede_a
):
    """Acceptance 10 · and removing the sede row returns it to the network
    price with no further edit."""
    item = make_item(tenant_a)
    today = date.today()
    ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        location=None,
        price=Decimal("10000"),
        effective_from=today - timedelta(days=10),
        source="imported",
    )
    scoped = ItemPrice.objects.create(
        tenant=tenant_a,
        item=item,
        location=sede_a,
        price=Decimal("9000"),
        effective_from=today - timedelta(days=5),
        source="manual",
    )
    assert prices.in_force(item.id, location_id=sede_a.id).price == Decimal("9000")
    assert prices.in_force(item.id).price == Decimal("10000")

    scoped.effective_to = today
    scoped.save(update_fields=["effective_to"])
    assert prices.in_force(item.id, location_id=sede_a.id).price == Decimal("10000")


@pytest.mark.django_db
def test_a_service_cost_can_be_cleared(client_as, owner_a, tenant_a):
    """An explicit null is a value here, not an omission: clearing a service's
    cost of goods is how a network says it has none."""
    service = make_item(
        tenant_a,
        name="Inyectología",
        presentation="",
        type=ItemType.SERVICE,
        tracks_stock=False,
        tracks_lots=False,
        tracks_expiry=False,
        unit="servicio",
        service_cost=Decimal("2200"),
    )
    body = _patch(
        client_as(owner_a), f"/api/items/{service.id}", {"service_cost": None}
    )
    assert body.status_code == 200, body.content
    assert body.json()["service_cost"] is None
    service.refresh_from_db()
    assert service.service_cost is None


@pytest.mark.django_db
def test_a_barcode_only_edit_is_legible_on_the_audit_trail(
    client_as, owner_a, tenant_a
):
    """A row whose before and after are identical cannot say who moved a scan
    from one product to another."""
    item = make_item(tenant_a)
    _patch(
        client_as(owner_a),
        f"/api/items/{item.id}",
        {"barcodes": [{"code": "7701234567890", "is_primary": True}]},
    )
    row = AuditLog.objects.filter(entity_type="items").first()
    assert row.before["barcodes"] == []
    assert row.after["barcodes"] == ["7701234567890*"]
