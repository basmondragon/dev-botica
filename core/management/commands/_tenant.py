"""The base every tenant-scoped management command inherits.

Ledger rule 6, context two: the tenant is an explicit required argument and the
command pins before doing any work. There is no "current tenant" for a shell,
and a command that guessed one would be a command that wrote into the wrong
network exactly once.
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from core.tenancy import pin_tenant, resolve_tenant_for_slug


class TenantCommand(BaseCommand):
    """`--tenant` is required, resolved, and pinned before `handle_tenant` runs."""

    tenant_help = "The tenant's slug or id. Required: a command has no current tenant."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help=self.tenant_help)
        self.add_tenant_arguments(parser)

    def add_tenant_arguments(self, parser):
        """Subclasses add their own arguments here."""

    def handle(self, *args, **options):
        tenant_id = resolve_tenant(options["tenant"])
        with pin_tenant(tenant_id):
            return self.handle_tenant(tenant_id, *args, **options)

    def handle_tenant(self, tenant_id, *args, **options):
        raise NotImplementedError


def resolve_tenant(value):
    """A tenant id from `--tenant`, accepting either a slug or an id."""
    try:
        return uuid.UUID(str(value))
    except ValueError:
        pass
    tenant_id = resolve_tenant_for_slug(str(value))
    if tenant_id is None:
        raise CommandError(f"No tenant has the slug {value!r}.")
    return tenant_id
