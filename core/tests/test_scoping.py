"""The location-scoping predicate (A2, acceptance 14)."""

import pytest
from ninja.errors import HttpError

from core import scoping
from core.models import LocationStatus, Role
from core.tests.conftest import make_location, make_user


@pytest.mark.django_db
def test_an_owner_reads_every_active_sede(tenant_a):
    for code in ("CHA", "KEN", "SUB"):
        make_location(tenant_a, code)
    owner = make_user(tenant_a, Role.OWNER, "owner@la45.co")
    assert len(scoping.readable_locations(owner, tenant_a.id)) == 3


@pytest.mark.django_db
def test_a_cashier_reads_exactly_their_home_sede(tenant_a):
    home = make_location(tenant_a, "CHA")
    make_location(tenant_a, "KEN")
    cashier = make_user(tenant_a, Role.CASHIER, "cashier@la45.co", location=home)
    assert scoping.readable_locations(cashier, tenant_a.id) == [home.id]


@pytest.mark.django_db
def test_a_network_read_gives_a_cashier_the_whole_network(tenant_a):
    home = make_location(tenant_a, "CHA")
    make_location(tenant_a, "KEN")
    cashier = make_user(tenant_a, Role.CASHIER, "cashier@la45.co", location=home)
    assert len(scoping.readable_locations(cashier, tenant_a.id, network_read=True)) == 2


@pytest.mark.django_db
def test_the_helper_raises_rather_than_defaulting(tenant_a):
    """A cashier with a null home sede is a misconfiguration. If this defaulted,
    they would silently see every sede's till -- and it would present as a UI bug
    rather than as an error."""
    from core.models import User

    make_location(tenant_a, "CHA")
    stray = User(
        tenant=tenant_a,
        role=Role.CASHIER,
        email="stray@la45.co",
        name="Stray",
        location=None,
    )
    with pytest.raises(scoping.Misconfigured):
        scoping.readable_locations(stray, tenant_a.id)


@pytest.mark.django_db
def test_a_scoped_query_narrows_and_never_silently_empties(tenant_a):
    """An explicit filter naming a location outside the identity's set is
    rejected, not intersected away."""
    home = make_location(tenant_a, "CHA")
    other = make_location(tenant_a, "KEN")
    cashier = make_user(tenant_a, Role.CASHIER, "cashier@la45.co", location=home)
    with pytest.raises(HttpError):
        scoping.readable_locations(cashier, tenant_a.id, requested=[other.id])


@pytest.mark.django_db
def test_a_closed_sede_is_not_in_the_office_default(tenant_a):
    make_location(tenant_a, "CHA")
    closed = make_location(tenant_a, "USM")
    closed.status = LocationStatus.CLOSED
    closed.save(update_fields=["status"])
    owner = make_user(tenant_a, Role.OWNER, "owner@la45.co")
    assert closed.id not in scoping.readable_locations(owner, tenant_a.id)


@pytest.mark.django_db
def test_the_ui_default_is_the_cashiers_own_sede(tenant_a):
    home = make_location(tenant_a, "CHA")
    cashier = make_user(tenant_a, Role.CASHIER, "cashier@la45.co", location=home)
    owner = make_user(tenant_a, Role.OWNER, "owner@la45.co")
    assert scoping.default_location(cashier) == home.id
    assert scoping.default_location(owner) is None
