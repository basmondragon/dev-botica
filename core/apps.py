from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Botica"

    def ready(self):
        from core import auth  # noqa: F401  -- registers the sign-in signals
        from core import tasks  # noqa: F401  -- registers the queue's jobs
