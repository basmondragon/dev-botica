"""Django settings for Botica.

Two things here are load-bearing and are stated in architecture.md §9:
`SET search_path` per tenant is prohibited -- it is schema-per-tenant in
disguise -- and the tenant pin is a transaction-scoped `SET LOCAL`, so it holds
only because every request is one transaction (`ATOMIC_REQUESTS`). The setting
itself is named in `core.tenancy` and in the policy migration, and nowhere
else.
"""

import ipaddress
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"{name} must be set")
    return value


SECRET_KEY = _env("DJANGO_SECRET_KEY", "insecure-dev-key-do-not-ship")
DEBUG = _env("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [
    h for h in _env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h
]
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h != "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.headless",
    "procrastinate.contrib.django",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.AdminConsoleMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "core.middleware.RequestIdMiddleware",
    "core.middleware.TenantMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "botica.urls"
WSGI_APPLICATION = "botica.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "web" / "dist", BASE_DIR / "core" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DB_ROLE = _env("BOTICA_DB_ROLE", "runtime")
if DB_ROLE not in ("runtime", "migration"):
    raise RuntimeError("BOTICA_DB_ROLE must be 'runtime' or 'migration'")

_IS_MIGRATION_ROLE = DB_ROLE == "migration"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _env("POSTGRES_DB", "botica"),
        "HOST": _env("POSTGRES_HOST", "localhost"),
        "PORT": _env("POSTGRES_PORT", "5432"),
        "USER": (
            _env("BOTICA_MIGRATION_USER", "botica_migrator")
            if _IS_MIGRATION_ROLE
            else _env("BOTICA_RUNTIME_USER", "botica_app")
        ),
        "PASSWORD": (
            _env("BOTICA_MIGRATION_PASSWORD", "")
            if _IS_MIGRATION_ROLE
            else _env("BOTICA_RUNTIME_PASSWORD", "")
        ),
        # `SET LOCAL app.tenant_id` lives only as long as its transaction, so the
        # pin holds only because every request is one (A1, architecture §9).
        "ATOMIC_REQUESTS": True,
        **(
            {}
            # Django's native psycopg3 pool. No pgbouncer: `LISTEN` needs a direct
            # connection, and a transaction-mode pooler breaks the worker first.
            if _IS_MIGRATION_ROLE
            else {"OPTIONS": {"pool": {"min_size": 2, "max_size": 10}}}
        ),
    }
}

BOTICA_RUNTIME_DB_USER = _env("BOTICA_RUNTIME_USER", "botica_app")

AUTH_USER_MODEL = "core.User"
AUTHENTICATION_BACKENDS = ["core.auth.TenantAuthenticationBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation."
        "UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Invite-only. No self-signup path exists and no registration endpoint is
# exposed, even behind a flag (architecture §3).
ACCOUNT_ADAPTER = "core.auth.InviteOnlyAccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_PREVENT_ENUMERATION = False

ACCOUNT_RATE_LIMITS = {
    "login": "30/m/ip",
    "login_failed": "10/m/ip,5/300s/key",
}

try:
    ALLAUTH_TRUSTED_PROXY_COUNT = int(_env("BOTICA_TRUSTED_PROXY_COUNT", "1"))
except ValueError as _error:
    raise RuntimeError(
        "BOTICA_TRUSTED_PROXY_COUNT must be a whole number of proxy hops."
    ) from _error

HEADLESS_ONLY = True
HEADLESS_CLIENTS = ("browser",)

# One locale, fixed. There is no i18n runtime and no second locale: the interface
# is Spanish (Colombia) and every identifier is English (architecture §1, §A.11).
LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = (
    [BASE_DIR / "web" / "dist"] if (BASE_DIR / "web" / "dist").exists() else []
)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# `users.email` is unique per tenant, not globally: one address may exist in
# two networks and they are two people. The authentication backend resolves
# the tenant through the sign-in lookup and pins before the credential check,
# so it never has to disambiguate.
SILENCED_SYSTEM_CHECKS = ["auth.W004"]

BOTICA_INSECURE_COOKIES = _env("BOTICA_INSECURE_COOKIES", "0") == "1"
SESSION_COOKIE_SECURE = not BOTICA_INSECURE_COOKIES
CSRF_COOKIE_SECURE = not BOTICA_INSECURE_COOKIES
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_HSTS_SECONDS = int(_env("BOTICA_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True

BOTICA_ADMIN_ALLOWED_IPS = [
    entry.strip()
    for entry in _env(
        "BOTICA_ADMIN_ALLOWED_IPS",
        "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7",
    ).split(",")
    if entry.strip()
]

for _entry in BOTICA_ADMIN_ALLOWED_IPS:
    try:
        ipaddress.ip_network(_entry, strict=False)
    except ValueError as _error:
        raise RuntimeError(
            f"BOTICA_ADMIN_ALLOWED_IPS carries {_entry!r}, which is not an "
            "address or CIDR."
        ) from _error

BOTICA_ADMIN_ALLOWED_HOSTS = [
    entry.strip()
    for entry in _env("BOTICA_ADMIN_ALLOWED_HOSTS", "").split(",")
    if entry.strip()
]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": _env("BOTICA_CACHE_ROOT", "/tmp/botica-cache"),
        "OPTIONS": {"MAX_ENTRIES": 10000},
    }
}

EMAIL_BACKEND = _env(
    "DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = _env("EMAIL_HOST", "")
EMAIL_PORT = int(_env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = _env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = _env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = _env("EMAIL_USE_TLS", "1") == "1"
EMAIL_USE_SSL = _env("EMAIL_USE_SSL", "0") == "1"
EMAIL_TIMEOUT = int(_env("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = _env("DJANGO_DEFAULT_FROM_EMAIL", "no-reply@localhost")

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise RuntimeError(
        "EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be on. Use TLS on port 587 "
        "or SSL on port 465, not both."
    )
if EMAIL_BACKEND == SMTP_BACKEND and not EMAIL_HOST:
    raise RuntimeError(
        "EMAIL_HOST must name the relay when DJANGO_EMAIL_BACKEND is SMTP."
    )

BOTICA_APP_URL = _env(
    "BOTICA_APP_URL", "https://" + (ALLOWED_HOSTS[0] if ALLOWED_HOSTS else "localhost")
)
BOTICA_INVITATION_TTL_DAYS = int(_env("BOTICA_INVITATION_TTL_DAYS", "7"))
BOTICA_INVITATION_MAX_ATTEMPTS = int(_env("BOTICA_INVITATION_MAX_ATTEMPTS", "5"))

BOTICA_VERSION = _env("BOTICA_VERSION", "") or "0.1.0"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact_secrets": {"()": "botica.redaction.RedactingFilter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "filters": ["redact_secrets"]}
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    # `django.server` carries its own handler in Django's defaults, and that
    # handler writes the request line -- the one place an invitation token in a
    # path would land. It is routed here instead, so every record Django emits
    # goes through the same scrubber Gunicorn's access log does.
    "loggers": {
        "django.server": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
