"""Write `schema/openapi.json`, from which the typed frontend client is generated."""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.api import api


class Command(BaseCommand):
    help = "Write the OpenAPI schema the typed client is generated from (§9)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--out", default=str(Path(settings.BASE_DIR) / "schema" / "openapi.json")
        )

    def handle(self, *args, **options):
        target = Path(options["out"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(api.get_openapi_schema(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.stdout.write(self.style.SUCCESS(f"wrote {target}"))
