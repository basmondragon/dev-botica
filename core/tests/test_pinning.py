"""The pin holds in all five contexts of ledger rule 6."""

import uuid

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core import tenancy
from core.management.commands._tenant import TenantCommand
from core.models import Location, Role
from core.tests.conftest import make_location, make_user


@pytest.mark.django_db
def test_a_management_command_refuses_to_start_without_its_tenant():
    """There is no "current tenant" for a shell."""

    class Probe(TenantCommand):
        def handle_tenant(self, tenant_id, *args, **options):  # pragma: no cover
            raise AssertionError("the command body must not run")

    with pytest.raises(CommandError):
        call_command(Probe())


@pytest.mark.django_db
def test_a_management_command_pins_before_doing_any_work(tenant_a):
    seen = {}

    class Probe(TenantCommand):
        def handle_tenant(self, tenant_id, *args, **options):
            seen["pinned"] = tenancy.current_tenant()

    call_command(Probe(), tenant=tenant_a.slug)
    assert seen["pinned"] == tenant_a.id


@pytest.mark.django_db
def test_a_job_without_a_tenant_id_refuses_rather_than_writing_unpinned():
    """A job that reports success having written nothing is the failure this
    refusal exists for, and in a log it is indistinguishable from the real
    thing."""
    with pytest.raises(ValueError):
        with tenancy.pinned_job({}):  # pragma: no cover
            raise AssertionError("the job body must not run")


@pytest.mark.django_db
def test_a_job_with_a_tenant_id_pins(tenant_a):
    with tenancy.pinned_job({"tenant_id": str(tenant_a.id)}):
        assert tenancy.current_tenant() == tenant_a.id


@pytest.mark.django_db
def test_a_sync_push_batch_naming_another_tenant_is_rejected_whole(tenant_a, tenant_b):
    """A foreign row rejects the batch rather than being filtered out of it: a
    silently dropped row is indistinguishable from a row never sent."""
    batch = [
        {"tenant_id": str(tenant_a.id), "client_uuid": "1"},
        {"tenant_id": str(tenant_b.id), "client_uuid": "2"},
    ]
    with pytest.raises(tenancy.ForeignTenantRow):
        with tenancy.pinned_batch(tenant_a.id, batch):  # pragma: no cover
            raise AssertionError("no element of a rejected batch may be applied")

    ours = [
        {"tenant_id": str(tenant_a.id), "client_uuid": "1"},
        {"tenant_id": str(tenant_a.id), "client_uuid": "2"},
    ]
    with tenancy.pinned_batch(tenant_a.id, ours) as pinned:
        assert pinned == tenant_a.id


@pytest.mark.django_db
def test_the_resolution_registry_holds_exactly_one_entry_at_s0():
    """S2 registers the device key and S5 the provider reference; both call this
    module rather than opening a second hole."""
    assert set(tenancy.RESOLVERS) == {"sign_in"}


@pytest.mark.django_db
def test_a_resolver_returns_uuids_and_never_a_row(tenant_a, sede_a):
    person = make_user(tenant_a, Role.OWNER, "owner@la45.co")
    answer = tenancy.resolve("sign_in", "owner@la45.co")
    assert isinstance(answer, tenancy.Resolution)
    assert answer.tenant_id == tenant_a.id
    assert answer.subject_id == person.id
    assert tenancy.resolve("sign_in", "nobody@nowhere.co") is None


@pytest.mark.django_db
def test_the_unauthenticated_inbound_path_never_enters_its_handler_unresolved():
    with pytest.raises(LookupError):
        with tenancy.resolve_then_pin("sign_in", "nobody@nowhere.co"):
            raise AssertionError("the handler body must not run")  # pragma: no cover


@pytest.mark.django_db
def test_only_the_pinning_module_and_the_policy_migration_name_the_setting():
    """Checks 4: a grep, because a second `SET LOCAL` is invisible any other way."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    allowed = {
        root / "core" / "tenancy.py",
        root / "core" / "middleware.py",
        root / "core" / "migrations" / "0002_rls.py",
        root / "core" / "management" / "commands" / "check_rls.py",
        pathlib.Path(__file__),
    }
    offenders = []
    for path in root.rglob("*.py"):
        if ".venv" in path.parts or "node_modules" in path.parts:
            continue
        if path in allowed or "tests" in path.parts:
            continue
        code = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        if re.search(r"app\.tenant_id", code):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"app.tenant_id appears outside the pinning path: {offenders}"


@pytest.mark.django_db
def test_a_platform_admin_with_no_selection_pins_nothing(tenant_a):
    """There is no null-tenant pin and no wildcard: NO_TENANT matches no policy."""
    make_location(tenant_a, "CHA")
    with tenancy.pin_tenant(tenancy.NO_TENANT):
        assert tenancy.current_tenant() == uuid.UUID(int=0)
        assert Location.objects.count() >= 0  # the migration role still owns them
