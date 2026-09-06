"""The API surface: identity, roles, invitations, people, settings, the grid."""

import json

import pytest

from core.models import (
    AuditLog,
    Invitation,
    InvitationStatus,
    Role,
    User,
    UserStatus,
)
from core.tests.conftest import make_location, make_user


def _post(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def _patch(client, path, payload):
    return client.patch(path, data=json.dumps(payload), content_type="application/json")


@pytest.mark.django_db
def test_me_answers_the_identity_the_shell_gates_its_nav_on(client_as, owner_a, sede_a):
    response = client_as(owner_a).get("/api/me")
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "owner"
    assert body["tenant"]["name"] == "Droguerías La 45"
    assert body["location_id"] is None
    assert sede_a.id is not None
    assert body["app_version"]


@pytest.mark.django_db
def test_nav_counters_report_zero_and_zero_renders_nothing(client_as, owner_a):
    """Zero renders nothing at all -- not a 0, not a dot, not a dimmed badge.

    S4 filled `counter` with ventas abiertas and S6 added `purchasing`, so the
    map is no longer empty; a tenant that has never sold reports zero on both,
    and the shell is what declines to draw them.

    **The assertion is the whole map and not a subset**, which is what makes a
    stage adding a key here a change somebody has to come and make on purpose.
    """
    response = client_as(owner_a).get("/api/nav-counters")
    assert response.json() == {
        "counters": {"counter": 0, "purchasing": 0},
        "critical": [],
    }


@pytest.mark.django_db
def test_a_cashier_asks_the_server_for_no_nav_counter(client_as, cashier_a):
    """A4 · the till is the read model that knows how many tickets are open on
    it, and a nav counter that needed the network would be the one number on a
    till surface that stops working when the cable comes out (§4)."""
    response = client_as(cashier_a).get("/api/nav-counters")
    assert response.json() == {"counters": {}, "critical": []}


@pytest.mark.django_db
def test_a_cashier_may_read_the_sedes_and_may_not_read_the_roster(client_as, cashier_a):
    client = client_as(cashier_a)
    assert client.get("/api/locations").status_code == 200
    denied = client.get("/api/users")
    assert denied.status_code == 403
    assert "Propietaria" in denied.json()["detail"]


@pytest.mark.django_db
def test_an_admin_may_invite_a_cashier_and_may_not_invite_an_admin(
    client_as, admin_a, sede_a
):
    client = client_as(admin_a)
    ok = _post(
        client,
        "/api/invitations",
        {"email": "nueva@la45.co", "role": "cashier", "location_id": str(sede_a.id)},
    )
    assert ok.status_code == 200, ok.content
    refused = _post(
        client, "/api/invitations", {"email": "otra@la45.co", "role": "admin"}
    )
    assert refused.status_code == 403


@pytest.mark.django_db
def test_an_invitation_at_cashier_needs_a_sede(client_as, owner_a):
    refused = _post(
        client_as(owner_a),
        "/api/invitations",
        {"email": "nueva@la45.co", "role": "cashier"},
    )
    assert refused.status_code == 422


@pytest.mark.django_db
def test_issuing_resending_and_revoking_all_land_an_audit_row(
    client_as, owner_a, sede_a
):
    client = client_as(owner_a)
    created = _post(
        client,
        "/api/invitations",
        {"email": "nueva@la45.co", "role": "cashier", "location_id": str(sede_a.id)},
    ).json()
    client.post(f"/api/invitations/{created['id']}/resend")
    client.delete(f"/api/invitations/{created['id']}")

    rows = list(
        AuditLog.objects.filter(entity_type="invitations").values_list(
            "action", flat=True
        )
    )
    assert sorted(rows) == ["create", "revoke", "send"]
    assert Invitation.objects.get(id=created["id"]).status == InvitationStatus.REVOKED


@pytest.mark.django_db
def test_the_accept_flow_creates_the_person_at_the_invited_role_and_sede(
    client, client_as, owner_a, sede_a
):
    created = _post(
        client_as(owner_a),
        "/api/invitations",
        {"email": "nueva@la45.co", "role": "cashier", "location_id": str(sede_a.id)},
    ).json()

    from core import invitations as service

    token = service.token_for(Invitation.objects.get(id=created["id"]))

    client.logout()
    preview = _post(client, "/api/invitations/preview", {"token": token})
    assert preview.status_code == 200
    assert preview.json()["tenant_name"] == "Droguerías La 45"

    accepted = _post(
        client,
        "/api/invitations/accept",
        {"token": token, "name": "Camila Rojas", "password": "una-clave-larga-2026"},
    )
    assert accepted.status_code == 200, accepted.content
    assert accepted.json()["landing"] == "/counter"

    person = User.objects.get(email="nueva@la45.co")
    assert person.role == Role.CASHIER
    assert person.location_id == sede_a.id

    # Opening the same link a second time is refused with a next step, not with
    # `Something went wrong`.
    again = _post(client, "/api/invitations/preview", {"token": token})
    assert again.status_code == 409
    assert again.json()["detail"] == "Esta invitación ya fue usada."


def test_a_path_shaped_token_reaches_the_shell_and_no_api_route():
    """`/accept/{token}` routes to a screen that neither previews nor accepts
    what is in it, and the access-log formatter scrubs that shape out of both
    the web server's line and Django's own records."""
    from django.urls import resolve

    from botica.redaction import scrub

    match = resolve("/accept/some-token-shape")
    assert match.url_name is None or not match.route.startswith("api/")
    assert "some-token-shape" not in scrub('GET /accept/some-token-shape HTTP/1.1" 200')


@pytest.mark.django_db
def test_role_change_is_owner_only_and_writes_before_and_after(
    client_as, owner_a, admin_a, cashier_a
):
    refused = _patch(
        client_as(admin_a), f"/api/users/{cashier_a.id}", {"role": "admin"}
    )
    assert refused.status_code == 403
    assert not AuditLog.objects.filter(entity_type="users", action="update").exists()

    ok = _patch(client_as(owner_a), f"/api/users/{cashier_a.id}", {"role": "admin"})
    assert ok.status_code == 200, ok.content
    row = AuditLog.objects.get(entity_type="users", action="update")
    assert row.before["role"] == "cashier"
    assert row.after["role"] == "admin"


@pytest.mark.django_db
def test_delete_is_owner_only_and_means_hard_delete(
    client_as, owner_a, admin_a, cashier_a
):
    assert client_as(admin_a).delete(f"/api/users/{cashier_a.id}").status_code == 403
    assert client_as(owner_a).delete(f"/api/users/{cashier_a.id}").status_code == 200
    assert not User.objects.filter(id=cashier_a.id).exists()
    assert AuditLog.objects.filter(entity_type="users", action="delete").exists()


@pytest.mark.django_db
def test_a_cashier_with_no_home_sede_is_unreachable_and_still_refused(
    tenant_a, cashier_a
):
    """The CHECK constraint makes the state unreachable through the API; the
    scoping helper is what makes it unreachable through a management command or
    a bad backfill too. Both are asserted, because each covers what the other
    cannot."""
    from django.db import IntegrityError, transaction

    from core import scoping

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            cashier_a.location = None
            cashier_a.save(update_fields=["location"])

    stray = User(
        tenant=tenant_a,
        role=Role.CASHIER,
        email="stray@la45.co",
        name="Stray",
        location=None,
    )
    with pytest.raises(scoping.Misconfigured) as refusal:
        scoping.readable_locations(stray, tenant_a.id)
    assert "sede" in str(refusal.value)


@pytest.mark.django_db
def test_the_settings_write_names_no_tenant_in_its_path_and_keeps_neighbours(
    client_as, owner_a, tenant_a
):
    from core import tenant_settings

    tenant_settings.write_group(tenant_a, "pricing", {"margin_goal": 22})
    response = _patch(
        client_as(owner_a),
        "/api/settings/tenant",
        {
            "name": "Droguerías La 45",
            "nit": "901.245.778-3",
            "legal_name": "Droguerías La 45 S.A.S.",
            "timezone": "America/Bogota",
        },
    )
    assert response.status_code == 200, response.content
    tenant_a.refresh_from_db()
    assert tenant_a.settings["pricing"] == {"margin_goal": 22}
    assert tenant_a.settings["tenant"]["legal_name"] == "Droguerías La 45 S.A.S."
    assert AuditLog.objects.filter(entity_type="tenants", action="update").exists()


@pytest.mark.django_db
def test_the_settings_read_states_status_and_the_write_cannot_set_it(
    client_as, owner_a
):
    body = client_as(owner_a).get("/api/settings/tenant").json()
    assert body["status"] == "active"
    assert "status" not in {field for field in ("status",) if field in _patch_fields()}


def _patch_fields():
    from core.api import TenantSettingsIn

    return set(TenantSettingsIn.model_fields)


@pytest.mark.django_db
def test_the_grid_contract_answers_row_count_after_filters_before_pagination(
    client_as, owner_a, tenant_a
):
    for index in range(30):
        make_user(tenant_a, Role.ADMIN, f"person{index}@la45.co")

    first = client_as(owner_a).get("/api/audit-log?page=1&page_size=25").json()
    assert first["page"] == 1
    assert first["page_size"] == 25
    assert isinstance(first["row_count"], int)

    people = client_as(owner_a).get("/api/users?page=2&page_size=25").json()
    assert people["row_count"] == User.objects.filter(tenant=tenant_a).count()
    assert people["page"] == 2


@pytest.mark.django_db
def test_an_unknown_sort_key_is_a_422_and_never_a_dropped_parameter(client_as, owner_a):
    response = client_as(owner_a).get("/api/audit-log?sort=invented")
    assert response.status_code == 422


@pytest.mark.django_db
def test_a_suspended_person_is_refused_on_their_next_request(client_as, owner_a):
    owner_a.status = UserStatus.SUSPENDED
    owner_a.save(update_fields=["status"])
    assert client_as(owner_a).get("/api/me").status_code in (401, 403)


@pytest.mark.django_db
def test_a_suspended_network_is_unreachable_by_its_own_members(
    client_as, owner_a, tenant_a
):
    from core.models import TenantStatus

    tenant_a.status = TenantStatus.SUSPENDED
    tenant_a.save(update_fields=["status"])
    response = client_as(owner_a).get("/api/locations")
    assert response.status_code == 403
    assert "suspendida" in response.json()["detail"]


@pytest.mark.django_db
def test_no_registration_path_is_exposed(client):
    for path in (
        "/_allauth/browser/v1/auth/signup",
        "/_allauth/browser/v1/account/password/reset",
    ):
        assert client.post(path).status_code == 404


@pytest.mark.django_db
def test_two_networks_see_none_of_each_others_rows(
    client_as, owner_a, tenant_b, sede_a
):
    other_sede = make_location(tenant_b, "EST", "La Estrella")
    body = client_as(owner_a).get("/api/locations").json()
    assert {row["id"] for row in body} == {str(sede_a.id)}
    assert str(other_sede.id) not in {row["id"] for row in body}


@pytest.mark.django_db
def test_a_cashier_lands_on_mostrador_and_reads_the_network(client_as, cashier_a):
    """§B.8.3 · a cashier reaches Mostrador and a read-only Inventario. The
    location list is a network read, which §2 grants them."""
    me = client_as(cashier_a).get("/api/me").json()
    assert me["role"] == "cashier"
    assert me["location_name"] == "Chapinero"
    assert len(me["readable_location_ids"]) == 1


@pytest.mark.django_db
def test_an_admin_may_not_invite_above_cashier_at_the_api(client_as, admin_a):
    """Absent in the interface, refused at the API. Both halves, always."""
    refused = _post(
        client_as(admin_a), "/api/invitations", {"email": "x@la45.co", "role": "owner"}
    )
    assert refused.status_code == 403
    assert not AuditLog.objects.filter(entity_type="invitations").exists()


@pytest.mark.django_db
def test_a_resend_does_not_rotate_the_token(client_as, owner_a, sede_a):
    """A link an owner already sent over another channel keeps working."""
    from core import invitations as service

    created = _post(
        client_as(owner_a),
        "/api/invitations",
        {"email": "nueva@la45.co", "role": "cashier", "location_id": str(sede_a.id)},
    ).json()
    invitation = Invitation.objects.get(id=created["id"])
    before = service.token_for(invitation)

    client_as(owner_a).post(f"/api/invitations/{created['id']}/resend")
    invitation.refresh_from_db()
    assert service.token_for(invitation) == before
    assert service.find_by_token(before) == invitation


@pytest.mark.django_db
def test_copiar_enlace_works_on_a_row_whose_delivery_failed(client_as, owner_a, sede_a):
    """Acceptance 11 · five failed deliveries are a channel failure and not an
    invalid invitation."""
    created = _post(
        client_as(owner_a),
        "/api/invitations",
        {"email": "nueva@la45.co", "role": "cashier", "location_id": str(sede_a.id)},
    ).json()
    Invitation.objects.filter(id=created["id"]).update(
        last_delivery_error="550 después de 5 intentos"
    )
    response = client_as(owner_a).post(f"/api/invitations/{created['id']}/link")
    assert response.status_code == 200
    assert "/accept#" in response.json()["accept_url"]


@pytest.mark.django_db
def test_sign_in_names_each_refusal_rather_than_folding_them_into_one(
    client, tenant_a, sede_a
):
    """§B.8.4·5 · a generic `Credenciales inválidas` tells an attacker nothing
    and tells a cashier nothing either."""
    from core.models import TenantStatus
    from core.tests.conftest import PASSWORD, make_user

    person = make_user(tenant_a, Role.OWNER, "owner@la45.co")

    def _refusal(email, password):
        response = client.post(
            "/_allauth/browser/v1/auth/login",
            data=json.dumps({"email": email, "password": password}),
            content_type="application/json",
        )
        errors = json.loads(response.content)["errors"]
        return " ".join(error["message"] for error in errors)

    assert "No encontramos una cuenta con ese correo." in _refusal(
        "nobody@la45.co", PASSWORD
    )
    assert "La contraseña no coincide" in _refusal(person.email, "not-the-password")

    person.status = UserStatus.SUSPENDED
    person.save(update_fields=["status"])
    assert "Su cuenta está suspendida" in _refusal(person.email, PASSWORD)

    person.status = UserStatus.ACTIVE
    person.save(update_fields=["status"])
    tenant_a.status = TenantStatus.SUSPENDED
    tenant_a.save(update_fields=["status"])
    assert "Esta droguería está suspendida" in _refusal(person.email, PASSWORD)

    # Never a generic one.
    assert "Credenciales" not in _refusal("nobody@la45.co", PASSWORD)


@pytest.mark.django_db
def test_the_service_worker_and_its_runtime_are_served_from_the_origin_root():
    """A worker's scope is its own directory, so `/static/sw.js` would control
    the assets and not the routes -- and `workbox-*.js` answered with the shell's
    HTML is a worker that precaches nothing, silently."""
    from django.urls import resolve

    for name in ("/sw.js", "/workbox-2fbc6a65.js", "/manifest.webmanifest"):
        assert resolve(name).url_name == "root-asset", name
    # And the shell still answers a route.
    assert resolve("/inventory").url_name != "root-asset"
