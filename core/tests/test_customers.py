"""`customers`, and the Ley 1581 deletion -- the legally load-bearing mutation
in this stage.

At S1 **nothing references `customers`, so every delete takes the first branch,
and that is the assertion here rather than a gap in it**: the endpoint counts
references over the tables that reference `customers`, S4 adds `sales` to that
count, and it adds no column to `customers` doing it. The erasure half is the
same check re-run in S4's session against the customer that fixture gives sales
to, and that is the only place it can honestly run.
"""

import json

import pytest

from core.models import AuditLog, Customer


def _post(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def _patch(client, path, payload):
    return client.patch(path, data=json.dumps(payload), content_type="application/json")


@pytest.fixture
def customer(tenant_a):
    return Customer.objects.create(
        tenant=tenant_a,
        document_type="CC",
        document="900000001",
        name="Hernando Villamil Ruiz",
        phone="+57 601 000 0000",
    )


@pytest.mark.django_db
def test_a_customer_no_sale_references_is_deleted_outright(
    client_as, owner_a, customer
):
    """Acceptance 25 · a 404 afterwards, not a row with empty fields."""
    before = client_as(owner_a).get("/api/customers").json()["row_count"]
    response = client_as(owner_a).delete(f"/api/customers/{customer.id}")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["outcome"] == "deleted"
    assert body["sale_count"] == 0

    assert client_as(owner_a).get(f"/api/customers/{customer.id}").status_code == 404
    after = client_as(owner_a).get("/api/customers").json()["row_count"]
    assert after == before - 1

    row = AuditLog.objects.get(entity_type="customers")
    assert row.actor_user_id == owner_a.id
    assert row.before["document"] == "900000001"
    assert row.after is None


@pytest.mark.django_db
def test_the_erasure_branch_counts_references_over_the_schema(customer):
    """S4 adds `sales` to this count without editing S1.

    The count is discovered from the relations pointing at `customers` rather
    than from a list here, which is what makes that true -- and at S1 there are
    none, so the assertion is that the count is zero and that nothing points at
    the table yet.
    """
    from core.catalog.api import _sales_referencing

    assert [
        relation.related_model.__name__ for relation in Customer._meta.related_objects
    ] == []
    assert _sales_referencing(customer) == 0


@pytest.mark.django_db
def test_cliente_eliminado_is_derived_and_no_column_records_it(
    tenant_a, client_as, owner_a
):
    """Acceptance 25 · the words are rendered from the absent name and document
    and are stored nowhere."""
    erased = Customer.objects.create(tenant=tenant_a)
    body = client_as(owner_a).get(f"/api/customers/{erased.id}").json()
    assert body["erased"] is True
    assert body["name"] == ""
    assert body["document"] == ""
    # And no boolean, timestamp or status on `customers` says a row was erased.
    columns = {field.name for field in Customer._meta.get_fields()}
    assert not {"erased", "erased_at", "deleted", "deleted_at", "anonymised"} & columns


@pytest.mark.django_db
def test_deleting_a_customer_is_owner_only(client_as, admin_a, customer):
    """Acceptance 25 · and in `Clientes` the action is absent rather than
    rendered disabled, which is the client's half of the same rule."""
    refused = client_as(admin_a).delete(f"/api/customers/{customer.id}")
    assert refused.status_code == 403
    assert Customer.objects.filter(id=customer.id).exists()


@pytest.mark.django_db
def test_consent_stamps_its_own_moment(client_as, owner_a, customer):
    assert customer.data_consent_at is None
    body = _patch(
        client_as(owner_a), f"/api/customers/{customer.id}", {"data_consent": True}
    ).json()
    assert body["data_consent"] is True
    assert body["data_consent_at"] is not None

    cleared = _patch(
        client_as(owner_a), f"/api/customers/{customer.id}", {"data_consent": False}
    ).json()
    assert cleared["data_consent_at"] is None


@pytest.mark.django_db
def test_one_customer_per_document_per_tenant(client_as, owner_a, customer, tenant_b):
    duplicate = _post(
        client_as(owner_a),
        "/api/customers",
        {"document_type": "CC", "document": "900000001", "name": "Otro"},
    )
    assert duplicate.status_code == 409

    # The same document in another network is a different person's, and the
    # constraint is per tenant.
    Customer.objects.create(
        tenant=tenant_b, document_type="CC", document="900000001", name="Ajeno"
    )
    assert client_as(owner_a).get("/api/customers").json()["row_count"] == 1


@pytest.mark.django_db
def test_a_document_needs_its_type(client_as, owner_a):
    refused = _post(
        client_as(owner_a), "/api/customers", {"document": "900000001", "name": "X"}
    )
    assert refused.status_code == 422


@pytest.mark.django_db
def test_the_seven_domestic_document_codes_are_what_is_stored(client_as, owner_a):
    """S5's per-target mapping translates them; nothing here spells them the
    way some invoicing system does (§8)."""
    for index, code in enumerate(("CC", "CE", "NIT", "TI", "PA", "PEP", "PPT")):
        response = _post(
            client_as(owner_a),
            "/api/customers",
            {
                "document_type": code,
                "document": f"90000000{index}",
                "name": f"Persona {index}",
            },
        )
        assert response.status_code == 200, (code, response.content)
        assert response.json()["document_type"] == code


@pytest.mark.django_db
def test_the_list_matches_document_and_name(client_as, owner_a, customer, tenant_a):
    Customer.objects.create(
        tenant=tenant_a, document_type="CE", document="123456", name="Marta Ospina"
    )
    client = client_as(owner_a)
    assert client.get("/api/customers?q=Villamil").json()["row_count"] == 1
    assert client.get("/api/customers?q=123456").json()["row_count"] == 1
    assert client.get("/api/customers?q=nadie").json()["row_count"] == 0
