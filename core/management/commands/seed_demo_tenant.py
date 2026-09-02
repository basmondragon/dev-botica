"""Build a synthetic tenant that looks like the handoff (architecture §1).

`--profile` is required and there is no default: a bare run refuses and prints
the five names, because a seed that guessed which shape you wanted is a seed
that quietly builds the wrong one.

The guard is two structural conditions and a failure writes nothing. First, the
command touches a tenant only if its `slug` begins `demo-` -- a slug is set at
provisioning and never changes, so no real network can acquire one. Second,
before any fixture runs, every table any registered fixture declares is counted
under the pin, and a single row the seed did not derive refuses the whole run.
"""

from django.core.management.base import BaseCommand, CommandError

from core.demo import identity, registry
from core.tenancy import pin_tenant

PREFIX = "demo-"


class Command(BaseCommand):
    help = "Build the demo tenant for one profile. Synthetic only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--profile",
            required=False,
            help="One of: " + ", ".join(registry.PROFILES),
        )

    def handle(self, *args, **options):
        profile = options.get("profile")
        if not profile:
            raise CommandError(
                "--profile is required. The five profiles are:\n  "
                + "\n  ".join(registry.PROFILES)
                + "\nEach builds its own tenant under its own slug, so two "
                "profiles share one database without colliding."
            )
        if profile not in registry.PROFILES:
            raise CommandError(
                f"{profile!r} is not a profile. The five are: "
                + ", ".join(registry.PROFILES)
            )

        slug = identity.slug_for(profile)
        if not slug.startswith(PREFIX):
            raise CommandError(
                f"The slug {slug!r} does not begin {PREFIX!r}. This command only "
                "ever touches synthetic tenants."
            )

        tenant_id = registry.uid(profile, "tenants", slug)
        context = registry.SeedContext(profile=profile, tenant_id=tenant_id, slug=slug)

        try:
            # One transaction per tenant, and the tenant row is written inside
            # the pin rather than before it.
            with pin_tenant(tenant_id):
                registry.run_profile(context)
        except registry.SeedRefused as refusal:
            raise CommandError(str(refusal)) from refusal

        self._report(context)

    def _report(self, context):
        """What it wrote, and what a check asserts against.

        The plaintext token of every outstanding invitation is printed here --
        the only place those tokens exist outside the email, and admissible only
        because the guard confines this command to synthetic tenants.
        """
        self.stdout.write(
            self.style.SUCCESS(
                f"seeded {context.slug} ({context.profile}) as {context.tenant_id}"
            )
        )
        for table in sorted(context.written):
            self.stdout.write(f"  {table:<16} {context.written[table]:>6}")
        for line in context.notes:
            self.stdout.write(line)
        self.stdout.write(
            f"  demo password    {identity.DEMO_PASSWORD}  (synthetic tenants only)"
        )
