"""Create the bring-up platform admin.

A platform admin belongs to no network: their `users` row carries a null
`tenant_id`, which no pin matches and no pinned insert may write. This command
therefore runs as the migration role, which owns the tables and bypasses RLS.
"""

import getpass

from django.core.management.base import BaseCommand, CommandError

from core.models import User


class Command(BaseCommand):
    help = "Create a platform admin, who belongs to no tenant."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--password", default=None)

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(f"{email} already exists.")
        password = options["password"] or getpass.getpass("Password: ")
        if len(password) < 12:
            raise CommandError("A platform admin's password is at least 12 characters.")
        User.objects.create_platform_admin(
            email=email, name=options["name"], password=password
        )
        self.stdout.write(self.style.SUCCESS(f"platform admin created: {email}"))
