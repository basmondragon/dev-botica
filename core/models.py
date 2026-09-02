"""The five tables S0 creates, and the two enums the ledger names for it.

Every table carries the architecture §3 convention -- `id` (uuid), `tenant_id`,
`created_at`, `updated_at`, an RLS policy and `FORCE ROW LEVEL SECURITY`. The
policies themselves live in the migrations, because Django has no vocabulary
for them and a policy nobody can read in SQL is a policy nobody audits.
"""

import uuid

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    """Four roles, one enum, one dependency (architecture §2)."""

    PLATFORM_ADMIN = "platform_admin", "Plataforma"
    OWNER = "owner", "Propietaria"
    ADMIN = "admin", "Administradora"
    CASHIER = "cashier", "Mostrador"


class LocationType(models.TextChoices):
    """A sede's kind. `distribution_center` exists from day one so that serving
    distributors is a configuration rather than a migration (A10). Nothing in v1
    creates one."""

    STORE = "store", "Punto de venta"
    WAREHOUSE = "warehouse", "Bodega"
    DISTRIBUTION_CENTER = "distribution_center", "Centro de distribución"


# Statuses are checked text, not Postgres enum types: the ledger names exactly two
# enums for S0 and no status enum, and a pilot that needs a sede `en montaje`
# before it opens should cost a CHECK change and a label rather than a type
# migration. The value sets are declared once here and shared with the API schema,
# so the typed client gets a literal union from the same source.
class TenantStatus(models.TextChoices):
    ACTIVE = "active", "Activa"
    SUSPENDED = "suspended", "Suspendida"


class UserStatus(models.TextChoices):
    ACTIVE = "active", "Activo"
    SUSPENDED = "suspended", "Suspendido"


class LocationStatus(models.TextChoices):
    ACTIVE = "active", "Activa"
    CLOSED = "closed", "Cerrada"


class InvitationStatus(models.TextChoices):
    """`expired` is derived from `expires_at` and is never stored -- a stored
    expiry is a second clock that has to be swept."""

    PENDING = "pending", "Pendiente"
    ACCEPTED = "accepted", "Aceptada"
    REVOKED = "revoked", "Revocada"


class AuditAction(models.TextChoices):
    """A closed vocabulary. Ten stages append to `audit_log`, and free text
    produces `role_changed`, `changed_role` and `ROLE_CHANGE` in one column
    within a year -- at which point the trail is readable by a person and not by
    a query. A stage needing an eleventh verb adds it here."""

    CREATE = "create", "Creó"
    UPDATE = "update", "Modificó"
    DELETE = "delete", "Eliminó"
    ARCHIVE = "archive", "Archivó"
    APPROVE = "approve", "Aprobó"
    REJECT = "reject", "Rechazó"
    SEND = "send", "Envió"
    REVOKE = "revoke", "Revocó"
    IMPERSONATE = "impersonate", "Entró como"


class TimestampedModel(models.Model):
    """`id`, `created_at`, `updated_at` -- the half of the convention every table
    carries, including the one that is not tenant-scoped."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantScopedModel(TimestampedModel):
    """The other half: `tenant_id`, which every policy is written against."""

    tenant = models.ForeignKey(
        "core.Tenant", on_delete=models.CASCADE, related_name="+"
    )

    class Meta:
        abstract = True


class Tenant(TimestampedModel):
    """A droguería network: one legal entity, one NIT, one catalog, many sedes.

    Its RLS policy is keyed on `id`, not `tenant_id` -- the table has no
    `tenant_id`, it *is* the tenant, and a policy written against a column that
    is not there is a policy that silently allows everything.
    """

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    nit = models.CharField(max_length=32, blank=True)
    status = models.CharField(
        max_length=20, choices=TenantStatus, default=TenantStatus.ACTIVE
    )
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "tenants"
        ordering = ["name"]
        constraints = [
            # Checked text, not a Postgres enum type: the ledger names exactly
            # two enums for S0 and no status enum. A pilot that needs a sede
            # `en montaje` before it opens should cost a CHECK change and a
            # label, not a type migration -- and that cheapness is the reason
            # for the choice.
            models.CheckConstraint(
                condition=models.Q(status__in=TenantStatus.values),
                name="tenant_status_is_declared",
            ),
        ]

    def __str__(self):
        return self.name


class Location(TenantScopedModel):
    """A **sede**: a shop floor, a warehouse, later a distribution centre."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=200)
    type = models.CharField(
        max_length=32, choices=LocationType, default=LocationType.STORE
    )
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    status = models.CharField(
        max_length=20, choices=LocationStatus, default=LocationStatus.ACTIVE
    )

    class Meta:
        db_table = "locations"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code"], name="one_location_code_per_tenant"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=LocationStatus.values),
                name="location_status_is_declared",
            ),
        ]

    def __str__(self):
        return f"{self.code} · {self.name}"


class UserManager(BaseUserManager):
    def create_platform_admin(self, *, email, name, password):
        """A platform admin belongs to no network, so their row carries a null
        `tenant_id` -- which no pin matches and no pinned insert may write."""
        user = self.model(
            email=self.normalize_email(email).lower(),
            name=name,
            role=Role.PLATFORM_ADMIN,
            platform_admin=True,
            tenant=None,
            location=None,
            is_staff=True,
            is_superuser=True,
            password=make_password(password),
        )
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        return self.create_platform_admin(
            email=email, name=extra.get("name", email), password=password
        )


class User(AbstractBaseUser, TimestampedModel):
    """A person. `tenant_id` is null for a `platform_admin`; `location_id` is the
    home **sede**, null meaning all locations for `owner` and `admin` (A2)."""

    email = models.EmailField()
    name = models.CharField(max_length=200)
    role = models.CharField(max_length=20, choices=Role)
    tenant = models.ForeignKey(
        Tenant, null=True, blank=True, on_delete=models.CASCADE, related_name="users"
    )
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        # Not SET_NULL: a cashier must have a home sede, so nulling one on the
        # way out would write the exact row `cashier_has_a_home_location`
        # forbids and fail the delete with a constraint violation instead of a
        # sentence. A sede with people homed to it is closed, not deleted.
        on_delete=models.PROTECT,
        related_name="users",
    )
    status = models.CharField(
        max_length=20, choices=UserStatus, default=UserStatus.ACTIVE
    )
    platform_admin = models.BooleanField(default=False)

    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        db_table = "users"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "email"], name="one_email_per_tenant"
            ),
            models.CheckConstraint(
                condition=models.Q(role=Role.PLATFORM_ADMIN, tenant__isnull=True)
                | (
                    ~models.Q(role=Role.PLATFORM_ADMIN) & models.Q(tenant__isnull=False)
                ),
                name="platform_admin_has_no_tenant",
            ),
            # A cashier with no home sede is a cashier who cannot open Mostrador
            # and whose failure is unattributable at a counter.
            models.CheckConstraint(
                condition=~models.Q(role=Role.CASHIER)
                | models.Q(location__isnull=False),
                name="cashier_has_a_home_location",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=UserStatus.values),
                name="user_status_is_declared",
            ),
        ]

    def __str__(self):
        return self.email

    @property
    def last_login_at(self):
        return self.last_login

    @property
    def is_active(self) -> bool:  # type: ignore[override]
        """`status` is the source of truth; Django's flag is derived from it."""
        return self.status == UserStatus.ACTIVE

    def has_perm(self, perm, obj=None):
        return self.is_superuser

    def has_module_perms(self, app_label):
        return self.is_superuser


class Invitation(TenantScopedModel):
    """Invite-only user creation. There is no self-signup path.

    Only `token_hash` is stored: the plaintext token exists in the email and
    nowhere else. Hashing at rest protects a stolen database; the access-log
    scrubber protects a log. They are different fixes for different threats and
    S0 does both.
    """

    email = models.EmailField()
    role = models.CharField(max_length=20, choices=Role)
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invitations",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL, related_name="invitations_sent"
    )
    expires_at = models.DateTimeField()
    status = models.CharField(
        max_length=20, choices=InvitationStatus, default=InvitationStatus.PENDING
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_delivery_error = models.TextField(blank=True)

    class Meta:
        db_table = "invitations"
        ordering = ["-created_at"]
        constraints = [
            # Two live invitations to one address is an ambiguity nobody can
            # resolve from the roster.
            models.UniqueConstraint(
                fields=["tenant", "email"],
                condition=models.Q(status=InvitationStatus.PENDING),
                name="one_pending_invitation_per_email_per_tenant",
            ),
            models.CheckConstraint(
                condition=~models.Q(role=Role.CASHIER)
                | models.Q(location__isnull=False),
                name="invited_cashier_has_a_home_location",
            ),
            models.CheckConstraint(
                condition=~models.Q(role__in=[Role.OWNER, Role.ADMIN])
                | models.Q(location__isnull=True),
                name="invited_office_role_has_no_home_location",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=InvitationStatus.values),
                name="invitation_status_is_declared",
            ),
        ]

    def __str__(self):
        return f"{self.email} → {self.tenant_id} ({self.role})"

    def accept_url(self, token):
        """The link an owner shares. The token travels in the fragment, which no
        browser puts on the wire -- so it reaches no request line, no access log
        and no `Referer`."""
        return f"{settings.BOTICA_APP_URL}/accept#{token}"

    @property
    def state(self):
        """The four rendered states. `expired` is derived here and never stored."""
        if self.status != InvitationStatus.PENDING:
            return self.status
        if self.expires_at <= timezone.now():
            return "expired"
        if self.last_delivery_error:
            return "delivery_failed"
        return InvitationStatus.PENDING


class AuditLog(TenantScopedModel):
    """The append-only record of every elevated-role mutation.

    Append-only is a database grant, not the discipline of eleven stage
    documents: the runtime role holds INSERT and SELECT on this table and does
    not hold UPDATE or DELETE.
    """

    # A stamped id, not a reference. Every stage referencing `users` does so
    # `ON DELETE SET NULL` and stamps the human-readable identity it needs at
    # write time -- and `audit_log` is the table where the second half of that
    # rule has to carry the whole weight, because the first half cannot run:
    # the runtime role holds no UPDATE on this table, so a cascade that nulled
    # the column would make every hard delete fail with a permission error.
    actor_user_id = models.UUIDField(null=True, blank=True)
    # Coined: `owner` may hard delete a user, and a mutation attributed to
    # nobody is not a record. This is what survives them.
    actor_email = models.EmailField()
    action = models.CharField(max_length=32, choices=AuditAction)
    entity_type = models.CharField(max_length=64)
    entity_id = models.UUIDField(null=True, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    # Coined: §B.10.3 requires a correlation id on every route-scope error, and a
    # mutation nobody can trace to a report is half a trail.
    request_id = models.CharField(max_length=64, blank=True)

    # `created_at` is a default rather than `auto_now_add` on this one table:
    # the demo seed has to be able to lay a real relative-time ladder down
    # (§B.9.1), and a row that can only ever be stamped `now()` cannot. Nothing
    # updates an audit row, so for every other caller this is the same value.
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "audit_log"
        ordering = ["-created_at"]
        # Ledger rule 4 · the reader's only ordering. Declared here so the
        # model state and the migration state agree.
        indexes = [
            models.Index(
                fields=["tenant", "-created_at"], name="audit_log_tenant_recent"
            )
        ]

    def __str__(self):
        return f"{self.action} {self.entity_type}"
