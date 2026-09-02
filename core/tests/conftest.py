"""Fixtures. The suite runs as the migration role, which owns the tables, and
becomes the runtime role wherever what is under test is a policy or a grant."""

import os

import pytest

os.environ.setdefault("BOTICA_DB_ROLE", "migration")

from django.conf import settings  # noqa: E402
from django.db import connection  # noqa: E402

from core.models import (  # noqa: E402
    Location,
    LocationType,
    Role,
    Tenant,
    User,
    UserStatus,
)

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Give the test database production's grant shape.

    `ALTER DEFAULT PRIVILEGES ... IN SCHEMA public` in `docker/initdb` is scoped
    to the database it ran in, and the test database is a different one. Without
    this the runtime role cannot read anything here, so every policy test would
    pass for the wrong reason.
    """
    runtime = settings.BOTICA_RUNTIME_DB_USER
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{runtime}"')
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
            f'TO "{runtime}"'
        )
        cursor.execute(
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{runtime}"'
        )
        # audit_log stays append-only here too, or the grant test proves nothing.
        cursor.execute(f'REVOKE UPDATE, DELETE ON audit_log FROM "{runtime}"')
    return django_db_setup


@pytest.fixture(autouse=True)
def _isolated_cache(settings):
    """A per-test cache.

    allauth's sign-in throttle counts failures in the default cache, which is
    file-backed and outlives the process. Sharing it between runs makes the
    refusal tests pass or fail depending on what ran before them.
    """
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "botica-tests",
        }
    }
    from django.core.cache import caches

    caches["default"].clear()


@pytest.fixture(autouse=True)
def _plain_staticfiles(settings):
    """The plain static files storage, so the suite needs no collectstatic."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }


@pytest.fixture
def as_runtime_role():
    """Become the runtime role for the rest of the current transaction.

    Every policy and every grant in this system is written for that role, and a
    test that checks one as the owner checks nothing.
    """

    def _become():
        with connection.cursor() as cursor:
            cursor.execute(f'SET LOCAL ROLE "{settings.BOTICA_RUNTIME_DB_USER}"')

    return _become


def make_tenant(name, slug):
    return Tenant.objects.create(name=name, slug=slug, nit="900.000.000-1")


def make_location(tenant, code, name="Sede", kind=LocationType.STORE):
    return Location.objects.create(tenant=tenant, code=code, name=name, type=kind)


def make_user(tenant, role, email, location=None, status=UserStatus.ACTIVE):
    user = User(
        tenant=tenant,
        role=role,
        email=email,
        name=email.split("@")[0],
        location=location,
        status=status,
    )
    user.set_password(PASSWORD)
    user.save()
    return user


@pytest.fixture
def tenant_a(db):
    return make_tenant("Droguerías La 45", "la-45")


@pytest.fixture
def tenant_b(db):
    return make_tenant("Farmacia La Estrella", "la-estrella")


@pytest.fixture
def sede_a(tenant_a):
    return make_location(tenant_a, "CHA", "Chapinero")


@pytest.fixture
def owner_a(tenant_a):
    return make_user(tenant_a, Role.OWNER, "owner@la45.co")


@pytest.fixture
def admin_a(tenant_a):
    return make_user(tenant_a, Role.ADMIN, "admin@la45.co")


@pytest.fixture
def cashier_a(tenant_a, sede_a):
    return make_user(tenant_a, Role.CASHIER, "cashier@la45.co", location=sede_a)


@pytest.fixture
def client_as(client):
    """Sign a person in and pin their session, the way the sign-in path does."""
    from core.middleware import SESSION_TENANT_KEY, SESSION_USER_KEY

    def _sign_in(user):
        client.force_login(user)
        session = client.session
        session[SESSION_TENANT_KEY] = str(user.tenant_id)
        session[SESSION_USER_KEY] = str(user.id)
        session.save()
        return client

    return _sign_in
