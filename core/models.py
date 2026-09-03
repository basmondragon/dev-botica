"""The tables and enums S0 and S1 create.

Every table carries the architecture §3 convention -- `id` (uuid), `tenant_id`,
`created_at`, `updated_at`, an RLS policy and `FORCE ROW LEVEL SECURITY`. The
policies themselves live in the migrations, because Django has no vocabulary
for them and a policy nobody can read in SQL is a policy nobody audits.

S0's five -- `tenants`, `locations`, `users`, `invitations`, `audit_log` -- come
first; S1's nine catalog tables follow under their own banner, then S2's two.

S2 also migrates six delta-cursor indexes onto S1's tables under ledger rule 4:
an index belongs to the stage whose read path needs it, and the read path is
`GET /api/sync/pull`. They are declared here, on the models S1 owns, because the
model state and the migration state have to agree.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower, Upper
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


# ---------------------------------------------------------------------------
# S1 · the catalog. Nine tables and four enums (ledger, architecture §3).
#
# Products and services are one table (A7): `tracks_stock = false` is the only
# thing that makes a service a service, and nothing downstream branches on the
# distinction. Every table below carries the same S0 convention as the five
# above -- `id`, `tenant_id`, `created_at`, `updated_at`, an RLS policy and
# FORCE ROW LEVEL SECURITY -- and the policies live in the migration.
#
# Four columns that read as "nullable" in architecture.md §3 are empty strings
# here rather than NULLs: `items.external_code`, `items.invima_registration`,
# `suppliers.nit` and `customers.document`. Each is the left half of a partial
# unique index, and three-valued logic on a uniqueness key is how two rows for
# the same product end up both legal. "Absent" is `''` throughout, exactly as
# S0 writes `tenants.nit`.
# ---------------------------------------------------------------------------


class Unaccented(models.Func):
    """`app_unaccent(text)` -- `Losartán` folded to `Losartan`.

    Postgres's own `unaccent(text)` is not IMMUTABLE, so it cannot appear in a
    generated column or in an index; the one-argument wrapper the migration
    declares is, because it names the dictionary explicitly. This is the whole
    reason a wrapper exists.
    """

    function = "app_unaccent"
    output_field = models.TextField()


def folded_name(field):
    """The expression behind every `search_name`: accents stripped, lowercased.

    A **generated column**, maintained by the database, and not a value the
    application writes: the catalog is written by an endpoint, by a management
    command and by a fixture, and a folded copy that any one of the three could
    forget to update is a search that quietly stops finding rows.
    """
    return Lower(Unaccented(field))


class EnumField(models.CharField):
    """A `CharField` whose column is a real Postgres enum type.

    S0 makes `role` and `location_type` real types by altering the column after
    the fact, because those columns already existed. A table created from
    scratch can simply declare the type, and there are two reasons to: the
    threat `choices` does not stop is "a management command or a bad backfill",
    which goes through no serializer; and Django's bulk insert casts each column
    to `db_type()[]` through `UNNEST`, so a varchar-shaped field over an enum
    column fails the insert outright rather than silently.

    The type itself is created by the first operation of migration 0005, before
    any table that names it.
    """

    def __init__(self, *args, db_enum="", **kwargs):
        self.db_enum = db_enum
        super().__init__(*args, **kwargs)

    def db_type(self, connection):
        return self.db_enum or super().db_type(connection)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["db_enum"] = self.db_enum
        return name, path, args, kwargs


class ItemType(models.TextChoices):
    """A7 · one table. The catalog holds everything a sede can put on a ticket."""

    PRODUCT = "product", "Producto"
    SERVICE = "service", "Servicio"


class VatClass(models.TextChoices):
    """Four values, not three rates and a spare (§3).

    `excluded` is not a taxable operation at all; `exempt` is taxable at 0% and
    carries a right to credit. The difference matters to an accountant and to
    the documento equivalente, which is why there are four.
    """

    EXCLUDED = "excluded", "Excluido de IVA"
    EXEMPT = "exempt", "Exento de IVA"
    RATE_5 = "rate_5", "IVA 5%"
    RATE_19 = "rate_19", "IVA 19%"


#: The class-to-rate mapping, as a **code constant owned by S1** -- not a table,
#: not a `tenants.settings` key, not a per-item number. The rates are statute; a
#: network does not get to set them and a table invites one to. A rate changed
#: by decree is a code change and a dated constant, which is survivable exactly
#: because `sale_lines` stamps `vat_class` and `tax_amount` at the moment of
#: sale (§3) -- so no historical ticket moves when this does.
VAT_RATES: dict[str, Decimal] = {
    VatClass.EXCLUDED: Decimal("0"),
    VatClass.EXEMPT: Decimal("0"),
    VatClass.RATE_5: Decimal("5"),
    VatClass.RATE_19: Decimal("19"),
}


class InvimaStatus(models.TextChoices):
    """Stored, not derived: two of the four are not derivable from a date.

    The nightly sweep keeps the one derivable transition honest -- `valid` past
    its date becomes `expired` -- and touches nothing else. `in_process` stays,
    because INVIMA has the file and that is the pharmacy's call, not Botica's.
    """

    VALID = "valid", "Registro vigente"
    IN_PROCESS = "in_process", "En trámite"
    EXPIRED = "expired", "Registro vencido"
    NOT_APPLICABLE = "not_applicable", "No aplica"


class PriceSource(models.TextChoices):
    """Two values, and there is no third (A11).

    No model writes a price, so there is no source to record one under. A price
    a person set after reading a suggestion is `manual` and carries their id and
    the `proposal_id` of what they read.
    """

    MANUAL = "manual", "Fijado por una persona"
    IMPORTED = "imported", "Cargado desde el sistema anterior"


#: The domestic vocabulary a Colombian counter actually uses. Stored readable at
#: rest; S5's per-target mapping translates it into whatever codes the client's
#: invoicing system expects (§8), so a system that spells these differently
#: costs seven lines in a mapping and no migration on this table.
#:
#: `''` is admitted by the CHECK and means **erased** -- the Ley 1581 deletion
#: clears the identifying fields in place, and a constraint that refused the
#: cleared row would make the erasure impossible.
DOCUMENT_TYPES = ("CC", "CE", "NIT", "TI", "PA", "PEP", "PPT")


class ImportKind(models.TextChoices):
    """Text, not a Postgres enum, deliberately: the demo seed writes
    `demo_seed` and S6 adds `sales_history` without migrating a type S1 owns.
    The cost is no database-level guard against a typo, which the loader's own
    validation covers."""

    CATALOG = "catalog", "Catálogo"
    DEMO_SEED = "demo_seed", "Datos de demostración"


class ImportStatus(models.TextChoices):
    RUNNING = "running", "En curso"
    COMPLETED = "completed", "Terminada"
    FAILED = "failed", "Falló"


class Manufacturer(TenantScopedModel):
    """The **laboratorio** (§3 glossary) -- Genfar, Tecnoquímicas, MK, La Santé,
    Procaps, Bayer, Baxter in the handoff data. Not a testing laboratory."""

    name = models.CharField(max_length=200)
    nit = models.CharField(max_length=32, blank=True)
    #: What the catalog's one search field matches a laboratorio on.
    search_name = models.GeneratedField(
        expression=folded_name("name"),
        output_field=models.TextField(),
        db_persist=True,
    )

    class Meta:
        db_table = "manufacturers"
        ordering = ["name"]
        # Rule 4 · S2's delta cursor. Tenant-wide, so the ledger's
        # `(tenant_id, location_id, updated_at, id)` shorthand narrows to four
        # columns here; the registry declares which shape each collection takes.
        indexes = [
            models.Index(
                fields=["tenant", "updated_at", "id"],
                name="manufacturers_delta_cursor",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"], name="one_manufacturer_name_per_tenant"
            ),
        ]

    def __str__(self):
        return self.name


class Category(TenantScopedModel):
    """Two levels, no deeper: flat enough to filter, nested enough to roll up.

    The depth limit is a trigger rather than a CHECK, because a row constraint
    cannot see the parent row it would have to read. It is enforced by the
    database either way, which is what the rule asks for.
    """

    name = models.CharField(max_length=120)
    parent = models.ForeignKey(
        "core.Category",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )

    class Meta:
        db_table = "categories"
        ordering = ["name"]
        # Rule 4 · S2's delta cursor, tenant-wide.
        indexes = [
            models.Index(
                fields=["tenant", "updated_at", "id"], name="categories_delta_cursor"
            ),
        ]
        constraints = [
            # NULLS NOT DISTINCT: without it two top-level categories could
            # share a name, because in SQL one NULL parent never equals another.
            models.UniqueConstraint(
                fields=["tenant", "name", "parent"],
                name="one_category_name_per_parent_per_tenant",
                nulls_distinct=False,
            ),
        ]

    def __str__(self):
        return self.name


class Supplier(TenantScopedModel):
    """A **proveedor** -- Coopidrogas in the handoff data, plus direct accounts.

    `lead_time_days` lands from the loader or by hand here and is overwritten by
    S6 from observed receiving (ledger).
    """

    nit = models.CharField(max_length=32, blank=True)
    name = models.CharField(max_length=200)
    contact = models.CharField(max_length=200, blank=True)
    payment_terms = models.CharField(max_length=120, blank=True)
    lead_time_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "suppliers"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "nit"],
                condition=~models.Q(nit=""),
                name="one_supplier_nit_per_tenant",
            ),
        ]

    def __str__(self):
        return self.name


class Item(TenantScopedModel):
    """A product **or** a service (A7). The catalog is the network's, not a
    sede's: nothing on this row names a location.

    `unit` is the **base unit**, and every quantity anywhere downstream in this
    product is in base units -- `stock_moves.quantity`, `sale_lines.quantity`,
    every order line, every forecast. `units_per_pack` is the only conversion
    and applies at receiving and at supplier cost.
    """

    # `type`, `vat_class` and `invima_status` are real Postgres enum types (see
    # `EnumField`): `choices` is a form-layer convenience that emits a plain
    # varchar and stops nothing.
    type = EnumField(max_length=16, choices=ItemType, db_enum="item_type")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    manufacturer = models.ForeignKey(
        Manufacturer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="items",
    )
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.PROTECT, related_name="items"
    )
    presentation = models.CharField(max_length=200, blank=True)
    active_ingredient = models.CharField(max_length=200, blank=True)
    strength = models.CharField(max_length=80, blank=True)

    # The registro INVIMA, first-class rather than a custom field (§3): it
    # expires on its own schedule, independent of any lot's expiry date, and a
    # droguería is inspected against it. Botica records the state and the
    # pharmacy's decision and validates nothing against INVIMA's register (§12).
    invima_registration = models.CharField(max_length=64, blank=True)
    invima_expires_at = models.DateField(null=True, blank=True)
    invima_status = EnumField(
        max_length=20, choices=InvimaStatus, db_enum="invima_status"
    )

    requires_prescription = models.BooleanField(default=False)
    controlled = models.BooleanField(default=False)
    cold_chain = models.BooleanField(default=False)

    unit = models.CharField(max_length=40)
    splittable = models.BooleanField(default=False)
    units_per_pack = models.PositiveIntegerField(default=1)

    vat_class = EnumField(max_length=16, choices=VatClass, db_enum="vat_class")

    # The three switches S3 reads to decide what a movement means. S1 writes
    # them and stops; there is no quantity anywhere in this stage.
    tracks_stock = models.BooleanField(default=True)
    tracks_lots = models.BooleanField(default=True)
    tracks_expiry = models.BooleanField(default=True)

    active = models.BooleanField(default=True)
    custom = models.JSONField(default=dict, blank=True)

    # Created empty here and written **only by S7** (ledger, disputed columns).
    # The cap belongs to the item rather than to a price row: it is a regulatory
    # property of the product set by the CNPMDM, and holding it on `item_prices`
    # would oblige every new price row to carry the previous one's cap forward
    # -- a guardrail that fails silently on exactly the reference somebody just
    # repriced (§11.4, A11). A null cap means *unknown*, never *uncapped*.
    regulated_max_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    cap_status = models.CharField(max_length=32, blank=True)

    # Coined: the previous system's own product code. It is what makes the load
    # tool idempotent and re-runnable, and it is the only stable handle a legacy
    # export has -- a barcode is not one, because an item legitimately carries
    # several and a droguería prints its own (§11.2).
    external_code = models.CharField(max_length=64, blank=True)

    # Coined: the standing cost of delivering one unit of a service, which is
    # what §3's "a service is a product with no cost of goods **unless one is
    # entered**" needs a home for. S4 stamps `sale_lines.unit_cost` from it
    # exactly as it stamps from the lot for a product. Null means no cost of
    # goods and a 100% margin.
    service_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    #: `losartan` finds `Losartán 50 mg × 30`, which is what a cashier types.
    #: Generated and stored by the database, with its own trigram index.
    search_name = models.GeneratedField(
        expression=folded_name("name"),
        output_field=models.TextField(),
        db_persist=True,
    )

    class Meta:
        db_table = "items"
        ordering = ["name"]
        indexes = [
            models.Index(
                fields=["tenant", "active", "name"], name="items_tenant_active_name"
            ),
            models.Index(fields=["tenant", "manufacturer"], name="items_tenant_lab"),
            models.Index(fields=["tenant", "category"], name="items_tenant_category"),
            models.Index(
                fields=["tenant", "invima_status"], name="items_tenant_invima_status"
            ),
            models.Index(
                fields=["tenant", "invima_expires_at"], name="items_tenant_invima_date"
            ),
            # A registro is matched **exactly** -- it is an identifier somebody
            # pasted, and a partial match on one is a wrong row -- so the index
            # that serves it is a btree and not the trigram the name gets. It
            # is over `upper(invima_registration)` because that is what the
            # case-insensitive match compiles to, and a btree on the bare column
            # cannot serve a predicate wrapped in a function.
            models.Index(
                Upper("invima_registration"),
                "tenant",
                name="items_tenant_invima_reg",
            ),
            # **Rule 4, and the stage is S2.** The delta pull's only query:
            # `WHERE tenant_id = $T AND (updated_at, id) > ($C1, $C2)
            #  AND updated_at <= $horizon ORDER BY updated_at, id LIMIT $n`.
            # `id` is the last column because the cursor is a tuple: without
            # it a page that ends inside a set of rows sharing one
            # microsecond has nowhere the next page can start.
            models.Index(
                fields=["tenant", "updated_at", "id"], name="items_delta_cursor"
            ),
        ]
        constraints = [
            # A7's whole negative: a service cannot track lots or expiry, and the
            # database says so rather than the form.
            models.CheckConstraint(
                condition=models.Q(tracks_stock=True)
                | (models.Q(tracks_lots=False) & models.Q(tracks_expiry=False)),
                name="untracked_item_moves_no_lots",
            ),
            models.CheckConstraint(
                condition=models.Q(units_per_pack__gte=1),
                name="units_per_pack_is_at_least_one",
            ),
            # A fraccionable item whose pack holds one base unit is not
            # fraccionable; it is a naming error waiting to divide by one.
            models.CheckConstraint(
                condition=models.Q(splittable=False) | models.Q(units_per_pack__gt=1),
                name="splittable_pack_holds_more_than_one",
            ),
            models.CheckConstraint(
                condition=models.Q(type=ItemType.SERVICE)
                | models.Q(service_cost__isnull=True),
                name="only_a_service_carries_a_service_cost",
            ),
            # Two rows for the same presentation of the same product is the
            # defect a catalog cleanup exists to remove.
            models.UniqueConstraint(
                fields=["tenant", "name", "presentation"],
                name="one_item_per_name_and_presentation_per_tenant",
            ),
            models.UniqueConstraint(
                fields=["tenant", "external_code"],
                condition=~models.Q(external_code=""),
                name="one_item_external_code_per_tenant",
            ),
        ]

    def __str__(self):
        return self.name


class ItemBarcode(TenantScopedModel):
    """Several codes per item is the normal case, not an anomaly: the
    manufacturer's EAN, the distributor's, and one the droguería printed itself.

    A code resolves to exactly one item within a tenant, because a cashier's
    scan must resolve to one item in under 50ms (§4) and an ambiguous scan sells
    the wrong product at the wrong price.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="barcodes")
    code = models.CharField(max_length=64)
    is_primary = models.BooleanField(default=False)

    class Meta:
        db_table = "item_barcodes"
        ordering = ["-is_primary", "code"]
        # Rule 4 · S2's delta cursor, tenant-wide. The collection's predicate
        # narrows to the registry's items, but that half is evaluated over the
        # page rather than in the WHERE clause -- `/api/sync/pull` has a p95
        # budget of 20 ms and anything that makes it a join is a defect.
        indexes = [
            models.Index(
                fields=["tenant", "updated_at", "id"],
                name="item_barcodes_delta_cursor",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code"], name="one_item_per_barcode_per_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "item"],
                condition=models.Q(is_primary=True),
                name="one_primary_barcode_per_item",
            ),
        ]

    def __str__(self):
        return self.code


class SupplierItem(TenantScopedModel):
    """What one proveedor charges for one item. Several suppliers per item is
    normal.

    `cost` is per **purchase pack**, not per base unit -- the base-unit rule
    divides it by `units_per_pack`. S6 overwrites it from what a receipt
    actually paid (ledger).
    """

    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="supplier_items"
    )
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="supplier_items"
    )
    supplier_code = models.CharField(max_length=64, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    min_order_pack = models.PositiveIntegerField(default=1)
    # Coined: which supplier S6 orders from when an item has several. Created
    # here under ledger rule 1 so S6 never migrates a table it does not own.
    is_preferred = models.BooleanField(default=False)

    class Meta:
        db_table = "supplier_items"
        ordering = ["supplier__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "supplier", "item"],
                name="one_link_per_supplier_and_item",
            ),
            models.UniqueConstraint(
                fields=["tenant", "item"],
                condition=models.Q(is_preferred=True),
                name="one_preferred_supplier_per_item",
            ),
            models.CheckConstraint(
                condition=models.Q(min_order_pack__gte=1),
                name="min_order_pack_is_at_least_one",
            ),
        ]

    def __str__(self):
        return f"{self.supplier_id} → {self.item_id}"


class ItemPrice(TenantScopedModel):
    """One price per item per scope per moment, always per **base unit**.

    A box price is `price × units_per_pack`, shown as a derived figure and never
    stored as a second row. A price is never edited and never deleted once it
    has been in force: a new row is created and the previous row's
    `effective_to` is closed in the same transaction, and a future-dated row
    becomes current by the resolution rule at read time rather than by a job.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="prices")
    #: Null means network-wide. A price scoped to a sede is a **sede** in the
    #: interface, and it is the one place a location appears in this stage.
    location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)
    #: The window is `[effective_from, effective_to)` -- the end is **exclusive**
    #: and null means open. Exclusive rather than inclusive so that closing a row
    #: on the day its replacement starts writes `effective_to = new
    #: effective_from` and leaves a zero-length window rather than a row whose
    #: end falls a day before its own start, which no CHECK could admit and
    #: which a same-day repricing would produce on the first try.
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    #: A real Postgres enum. It holds two values and there is no third, which is
    #: what makes A11 a property of the schema rather than a policy somebody
    #: could relax.
    source = EnumField(max_length=16, choices=PriceSource, db_enum="price_source")

    # Created here **with no foreign key at S1**: it names the S7 suggestion a
    # person acted on, and `price_proposals` is S7's table and does not exist
    # yet. S7 adds the constraint when it creates that table -- ledger rule 4's
    # one permitted exception to rule 1. Nullable is the ordinary case: most
    # price edits have no suggestion in play (A11).
    proposal_id = models.UUIDField(null=True, blank=True)

    # Who set this price, which is the question every price dispute starts with.
    # Null exactly when no person typed it -- every `imported` row the load tool
    # writes, for the same reason `imports.started_by_user_id` is nullable: a
    # management command has no HTTP user.
    set_by_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Coined, and it is S0's own referential rule rather than a second opinion:
    # "every stage referencing `users` does so ON DELETE SET NULL **and stamps
    # the human-readable identity it needs at write time**". A price whose
    # author was later hard-deleted would otherwise read as one nobody typed,
    # which is exactly the thing `source = manual` says is false -- and the
    # Precio history is the screen `set_by_user_id` exists to fill.
    set_by_name = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "item_prices"
        ordering = ["-effective_from"]
        indexes = [
            models.Index(
                fields=["tenant", "item", "location", "effective_from"],
                name="item_prices_resolution",
            ),
            # Rule 4 · S2's delta cursor, in the ledger's own four-column shape,
            # because this is the one S2 collection whose predicate is
            # location-scoped. Its predicate admits `location_id IS NULL` as
            # well, so the pull runs the tuple scan twice and merges rather than
            # making the index partial.
            models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="item_prices_delta_cursor",
            ),
        ]
        constraints = [
            # One open row per item per scope, always. NULLS NOT DISTINCT,
            # because two open network-wide rows is exactly the collision this
            # index exists to refuse and a NULL `location_id` would let through.
            models.UniqueConstraint(
                fields=["tenant", "item", "location"],
                condition=models.Q(effective_to__isnull=True),
                name="one_open_price_per_item_and_scope",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="price_window_does_not_close_before_it_opens",
            ),
        ]

    def __str__(self):
        return f"{self.item_id} · {self.price}"


class Customer(TenantScopedModel):
    """Master data, not a CRM (§12): `customers` exists to identify the
    acquirer on a fiscal document and to recognise a returning customer.

    S1 creates and loads them; **S4 is the only interactive writer** -- a
    customer is created at the counter, and offline (ledger rule 8).

    A customer any sale references is never hard-deleted: the identifying fields
    are erased in place and the row survives, because S5 re-renders the acquirer
    from the current row on every send rather than snapshotting it. No column
    records that -- the absent name and document are what `Cliente eliminado` is
    derived from, and `audit_log` answers who erased it and when.
    """

    document_type = models.CharField(max_length=8, blank=True)
    document = models.CharField(max_length=32, blank=True)
    name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=300, blank=True)
    data_consent = models.BooleanField(default=False)
    # Coined: a boolean alone cannot answer *when* consent was given, and Ley
    # 1581 asks (§7).
    data_consent_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "customers"
        ordering = ["name"]
        # Rule 4 · S2's delta cursor, tenant-wide and windowed by
        # `customer_recency_months` -- the window is evaluated over the page, so
        # the scan itself stays the plain tuple range.
        indexes = [
            models.Index(
                fields=["tenant", "updated_at", "id"], name="customers_delta_cursor"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "document_type", "document"],
                condition=~models.Q(document=""),
                name="one_customer_per_document_per_tenant",
            ),
            models.CheckConstraint(
                condition=models.Q(document_type="")
                | models.Q(document_type__in=DOCUMENT_TYPES),
                name="customer_document_type_is_declared",
            ),
        ]

    def __str__(self):
        return self.name or "Cliente eliminado"

    @property
    def erased(self) -> bool:
        """Derived, never stored. A flag would be a second truth to keep in step
        with the empty fields."""
        return not self.name and not self.document


class ImportRun(TenantScopedModel):
    """One row per run of a load tool, dry runs included.

    §3 names this table and no column of it; every column here is coined. S6's
    sales-history loader records its runs in the same table, and the demo seed
    writes one row per run with `kind = 'demo_seed'` -- which is both the record
    of the run and the marker that says the tenant is synthetic.
    """

    kind = models.CharField(max_length=32)
    source = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=16, default=ImportStatus.RUNNING)
    dry_run = models.BooleanField(default=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    rows_read = models.PositiveIntegerField(default=0)
    rows_created = models.PositiveIntegerField(default=0)
    rows_updated = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)
    #: Per failed row: its file, its line number, the value that failed and the
    #: reason. Read back by `GET /api/imports`, which is how an administrator
    #: sees what onboarding did to their catalog without asking us.
    errors = models.JSONField(default=list, blank=True)
    #: Null for a management command, which has no HTTP user.
    started_by_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "imports"
        ordering = ["-started_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=ImportStatus.values),
                name="import_status_is_declared",
            ),
        ]

    def __str__(self):
        return f"{self.kind} · {self.status}"


# ---------------------------------------------------------------------------
# S2 · sync. Two tables and three enums (ledger, architecture §3, §5).
#
# `devices` is the unit of sync and of blame: a browser at a counter that has
# been named, given a sede and handed a `device_key`. `sync_conflicts` is the
# office's arrival queue for everything the protocol refused or could not
# settle -- S2 writes the protocol rows, S3 the negative-stock rows and S4 the
# two divergences an offline sale brings with it.
#
# Both carry the same S0 convention as every table above, and their policies
# live in migration 0008 beside S0's and S1's.
# ---------------------------------------------------------------------------


class DeviceStatus(models.TextChoices):
    """Two values, not three. A device is claimed in the same request that
    creates it, so there is no `pending`."""

    ACTIVE = "active", "Activo"
    REVOKED = "revoked", "Dado de baja"


class SyncConflictType(models.TextChoices):
    """**Every value is declared here, at creation, and no later stage runs
    `ALTER TYPE`** (ledger, enum register).

    The first six are S2's own protocol refusals. `negative_stock` is written by
    S3 and `stale_price` and `catalog_divergence` by S4 -- declaring a value is
    not writing one. The reason is build order rather than courtesy: a value
    added by the stage that writes it is a migration that has to land before the
    stage that reads it, which is a coordination bug waiting for a clean build.
    """

    FOREIGN_TENANT = "foreign_tenant", "Fila de otra droguería"
    FOREIGN_LOCATION = "foreign_location", "Fila de otra sede"
    UNKNOWN_COLLECTION = "unknown_collection", "Colección desconocida"
    PAYLOAD_REJECTED = "payload_rejected", "Datos rechazados"
    DEVICE_REVOKED = "device_revoked", "Equipo dado de baja"
    DEVICE_SILENT = "device_silent", "Equipo sin sincronizar"
    NEGATIVE_STOCK = "negative_stock", "Existencias en negativo"
    STALE_PRICE = "stale_price", "Precio desactualizado"
    CATALOG_DIVERGENCE = "catalog_divergence", "Catálogo divergente"


class SyncConflictStatus(models.TextChoices):
    """`dismissed` exists because an administrator must be able to close a row
    without claiming a correction was made, and a queue with only one exit is a
    queue nobody keeps."""

    OPEN = "open", "Abierto"
    RESOLVED = "resolved", "Resuelto"
    DISMISSED = "dismissed", "Descartado"


class Device(TenantScopedModel):
    """A browser install that sells: its sede, its label, its hashed key.

    §3 names `location_id`, `label`, `device_key`, `last_seen_at` and
    `last_synced_at`, and says it lists only the columns that carry a decision;
    the other seven are coined here under ledger rule 1.

    **It carries no fiscal role.** Numbering leases are not built at v1 (A6), so
    a device is never the holder of a range.
    """

    location = models.ForeignKey(
        Location,
        # A sede holding tills is closed, not deleted -- the same reasoning
        # `users.location` takes, and for the same reason: nulling it on the way
        # out would leave a device that syncs nothing and blames nobody.
        on_delete=models.PROTECT,
        related_name="devices",
    )
    label = models.CharField(max_length=60)

    #: Coined here under rule 1, and *Demo seed* is what requires it: every
    #: seeded till carries one, and **`sales.number` is composed from it** --
    #: which is what makes a seeded ticket read like the handoff's
    #: `Venta C3-4821` rather than like a row from a generator. S4 reads it and
    #: writes none of it; a stage that had to migrate this column onto a table
    #: it neither creates nor writes would be doing what rule 1 forbids.
    #:
    #: Network-wide unique rather than per-sede, because it is one component of
    #: a sale number that must not repeat anywhere in the network.
    code = models.CharField(max_length=16)

    #: **Returned once, at claim, and stored hashed.** A key that can be read
    #: back from the server is a key that leaks through the office list. This is
    #: `sha256` and not a password hash on purpose: the key is 256 bits of
    #: `secrets` output, so there is no dictionary to slow down, and a per-call
    #: bcrypt on the hot sync path would cost more than it protects.
    device_key_hash = models.CharField(max_length=64, unique=True)

    status = EnumField(
        max_length=16,
        choices=DeviceStatus,
        db_enum="device_status",
        default=DeviceStatus.ACTIVE,
    )

    #: Any `/api/sync/*` call. `last_synced_at` is a completed pull and
    #: `last_pushed_at` a completed push -- three stamps because "quiet" and
    #: "talking but not draining" are different support calls.
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_pushed_at = models.DateTimeField(null=True, blank=True)

    #: The device's wall clock minus the server's, in milliseconds, as measured
    #: on the last call. Displayed and **never** used to correct `occurred_at`
    #: (§5 rule 4).
    clock_skew_ms = models.IntegerField(null=True, blank=True)

    #: **Nullable, and null is "not yet reported" -- never false.** A browser
    #: that has not answered `navigator.storage.persist()` has not refused it.
    storage_persisted = models.BooleanField(null=True, blank=True)

    app_version = models.CharField(max_length=32, blank=True)

    enrolled_by_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    enrolled_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "devices"
        ordering = ["location__name", "label"]
        constraints = [
            # Two tills at one sede called `Caja 1` is a support call the day
            # somebody reads a conflict report.
            models.UniqueConstraint(
                fields=["tenant", "location", "label"],
                name="one_device_label_per_location",
            ),
            models.UniqueConstraint(
                fields=["tenant", "code"], name="one_device_code_per_tenant"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=DeviceStatus.values),
                name="device_status_is_declared",
            ),
        ]

    def __str__(self):
        return f"{self.label} · {self.location_id}"


class SyncConflict(TenantScopedModel):
    """The office's arrival queue: what the protocol refused, and what a later
    stage could not settle.

    **`detail` never carries the rejected payload verbatim.** It carries the
    collection, the failing field, the reason code and the correlation id. A
    rejected `customers` row contains a person's document number, and a conflict
    queue is not a place to accumulate identifying data nobody asked to store
    (Ley 1581, §7's reasoning one table over).
    """

    #: Nullable: S3's negative-stock rows may name no device.
    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    #: The registry name, as text -- `items`, `customers`, later `sales`.
    collection = models.CharField(max_length=64, blank=True)
    client_uuid = models.UUIDField(null=True, blank=True)
    type = EnumField(
        max_length=32, choices=SyncConflictType, db_enum="sync_conflict_type"
    )
    detail = models.JSONField(default=dict, blank=True)
    status = EnumField(
        max_length=16,
        choices=SyncConflictStatus,
        db_enum="sync_conflict_status",
        default=SyncConflictStatus.OPEN,
    )
    #: The device's clock, stored exactly as it sent it. Null where the row was
    #: raised by the server itself, which is every `device_silent`.
    occurred_at = models.DateTimeField(null=True, blank=True)
    #: The server's, and what every report reads (§5 rule 4).
    recorded_at = models.DateTimeField(default=timezone.now)
    resolved_by_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    class Meta:
        db_table = "sync_conflicts"
        ordering = ["-recorded_at"]
        indexes = [
            # The queue's only ordering, and the filter the office opens it on.
            models.Index(
                fields=["tenant", "status", "-recorded_at"],
                name="sync_conflicts_queue",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=SyncConflictStatus.values),
                name="sync_conflict_status_is_declared",
            ),
            models.CheckConstraint(
                condition=models.Q(type__in=SyncConflictType.values),
                name="sync_conflict_type_is_declared",
            ),
        ]

    def __str__(self):
        return f"{self.type} · {self.collection}"


# ---------------------------------------------------------------------------
# S3 · inventory. Seven tables and one enum (ledger, architecture §3, §6).
#
# **No code updates a quantity in place** (A3). `stock_moves` is append-only --
# structurally, because migration 0010 revokes UPDATE and DELETE on it from the
# runtime role, exactly as S0 does for `audit_log` -- and `stock_on_hand` is a
# projection maintained inside the same transaction as the moves that change it
# by `core.inventory.ledger` and by nothing else (ledger rule 7).
#
# Every table below carries the same S0 convention as the fourteen above, and
# the policies live in migration 0010 beside S0's, S1's and S2's.
#
# **Quantities are signed integers in base units.** `items.unit` fixes the base
# unit and `units_per_pack` is the only conversion (§3): tablets for a
# splittable blister, boxes for one that is not. Integer rather than decimal
# because there is no fractional base unit in the catalog -- a `sobre 27,5 g` is
# one sobre -- and a decimal column would render `412,000` in a grid whose whole
# job is to be read at a glance.
# ---------------------------------------------------------------------------


class StockMoveType(models.TextChoices):
    """Every value, created complete by S3, with the causing stage fixed in the
    ledger rather than chosen at build time (enum register).

    S3 causes `transfer_out`, `transfer_in`, `adjustment`, `shrinkage`, `expiry`
    and `count`; S4 causes `sale` and `customer_return`; S6 causes `receipt` and
    `supplier_return`. In every case the row is written by S3's ledger service.
    """

    RECEIPT = "receipt", "Recepción"
    SALE = "sale", "Venta"
    CUSTOMER_RETURN = "customer_return", "Devolución de cliente"
    SUPPLIER_RETURN = "supplier_return", "Devolución a proveedor"
    TRANSFER_OUT = "transfer_out", "Traslado · salida"
    TRANSFER_IN = "transfer_in", "Traslado · entrada"
    ADJUSTMENT = "adjustment", "Ajuste"
    SHRINKAGE = "shrinkage", "Merma"
    EXPIRY = "expiry", "Vencimiento"
    COUNT = "count", "Conteo"


#: The types whose quantity must be positive, and the ones whose quantity must
#: be negative. `adjustment` and `count` are the two that take either sign, and
#: they are the two that reconcile a record to a shelf. A move of zero is not a
#: movement and is refused by its own CHECK.
POSITIVE_MOVE_TYPES = (
    StockMoveType.RECEIPT,
    StockMoveType.CUSTOMER_RETURN,
    StockMoveType.TRANSFER_IN,
)
NEGATIVE_MOVE_TYPES = (
    StockMoveType.SALE,
    StockMoveType.SUPPLIER_RETURN,
    StockMoveType.TRANSFER_OUT,
    StockMoveType.SHRINKAGE,
    StockMoveType.EXPIRY,
)

#: The three types a human writes by hand, and the only three
#: `POST /api/stock-moves` accepts. Every other value is the consequence of a
#: document that has its own endpoint.
DIRECT_MOVE_TYPES = (
    StockMoveType.ADJUSTMENT,
    StockMoveType.SHRINKAGE,
    StockMoveType.EXPIRY,
)

#: The types that carry a `reason`, and the only ones that may.
REASONED_MOVE_TYPES = (*DIRECT_MOVE_TYPES, StockMoveType.COUNT)

#: The reason vocabulary, **a constrained text column rather than a Postgres
#: enum** so a later stage adds a value without a migration. Null -- `''` here,
#: the convention S1 fixed -- on every type outside `REASONED_MOVE_TYPES`.
STOCK_MOVE_REASONS = (
    "opening_stock",
    "standalone_receipt",
    "correction",
    "damage",
    "theft",
    "loss",
    "expired",
    "count_adjustment",
    "negative_resolution",
)


class PolicySource(models.TextChoices):
    """Checked text, not a Postgres enum: the register names exactly one enum
    for S3. **S3 writes `manual`; S6 writes `model`**, and this column is what
    stops S6 erasing a threshold a pharmacist set on purpose (ledger)."""

    MANUAL = "manual", "Fijado por una persona"
    MODEL = "model", "Calculado por el modelo"


class TransferStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    DISPATCHED = "dispatched", "Despachado"
    RECEIVED = "received", "Recibido"
    PARTIAL = "partial", "Recibido parcial"


class TransferResolution(models.TextChoices):
    """How a shortfall was closed. Each writes a move: the remainder arrived
    late, or it did not arrive. A shortfall that quietly nets out of the ledger
    is merchandise nobody ever has to explain."""

    RECEIVED_LATE = "received_late", "Llegó después"
    LOST_IN_TRANSIT = "lost_in_transit", "No llegó"


class CountScope(models.TextChoices):
    FULL = "full", "Toda la sede"
    CATEGORY = "category", "Una categoría"
    ITEM_LIST = "item_list", "Una lista de productos"


class CountStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    COUNTING = "counting", "En conteo"
    CLOSED = "closed", "Cerrado"


class Lot(TenantScopedModel):
    """A **lote**: the grain at which a pharmaceutical unit is identified (§6).

    Expiry, supplier, acquisition cost and the lot's own sanitary registration
    attach here and not to the item. **There is no `location_id`** -- a lot is a
    property of merchandise and the same lot sits in several sedes, which is
    what makes `Quiebre · hay 96 en Suba` and an INVIMA withdrawal answerable at
    all.
    """

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="lots")
    lot_code = models.CharField(max_length=64)
    #: Null **only** where `items.tracks_expiry` is false.
    expires_at = models.DateField(null=True, blank=True)
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="lots"
    )
    #: What these units cost to acquire, per base unit. Every valuation in the
    #: product is `Σ quantity × unit_cost` and never a sale price -- an
    #: inventory-at-risk figure priced at retail overstates the loss by the
    #: whole margin. **S6 writes what a receipt actually paid** (ledger).
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    #: The lot's own sanitary registration, which is not the item's (§3).
    invima_registration = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "lots"
        ordering = ["expires_at", "lot_code"]
        indexes = [
            # Rule 4 · S2's delta cursor, **without `location_id`**, which this
            # table does not have. A deliberate departure from the shape the
            # rule names: the sync predicate joins through `stock_on_hand`
            # instead (see the registry amendment).
            models.Index(
                fields=["tenant", "updated_at", "id"], name="lots_delta_cursor"
            ),
            # The expiry horizons: `GET /api/stock/expiring`, the digest and the
            # `Vencimiento` chip all read a window on this column.
            models.Index(fields=["tenant", "expires_at"], name="lots_tenant_expiry"),
            # The reverse lookup a recall starts from: a lot code to every
            # location holding it.
            models.Index(fields=["tenant", "lot_code"], name="lots_tenant_code"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "item", "lot_code"],
                name="one_lot_code_per_item_per_tenant",
            ),
        ]

    def __str__(self):
        return self.lot_code


class StockMove(TenantScopedModel):
    """One movement of stock. **Append-only, and enforced by a grant** (A3).

    No endpoint, job, admin action or migration in this product updates or
    deletes a row here. A mistake is corrected by a second row, never by editing
    the first -- which is what makes appends commute, and what makes two offline
    tills selling the same last box a sum rather than a lost update.
    """

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(
        Lot, null=True, blank=True, on_delete=models.PROTECT, related_name="moves"
    )
    #: Signed, in base units. Never zero: a move of nothing is not a movement.
    quantity = models.IntegerField()
    type = EnumField(max_length=24, choices=StockMoveType, db_enum="stock_move_type")

    #: What caused it. `''` and null mean **the move is its own document**,
    #: which is true only of the three direct types -- `''` for the text half
    #: because that is the convention S1 fixed for an absent string.
    document_type = models.CharField(max_length=32, blank=True)
    document_id = models.UUIDField(null=True, blank=True)

    #: The cost at which **these** units moved, so a cost change next month does
    #: not retroactively rewrite last month's margin. Null means no cost was
    #: recorded, never zero (§B.9.2 tier 3).
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    #: The device's clock, stored exactly as it sent it, and the server's, which
    #: every report reads (§5 rule 4).
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(default=timezone.now)

    #: **A reference, not a nullable one.** A device that has moved stock is
    #: revoked and never deleted -- the rule S2 already follows for every till,
    #: and the rule `locations` already follows for a sede holding them. `PROTECT`
    #: is what makes it structural: `SET_NULL` would issue an `UPDATE` on this
    #: table on the way out, and the runtime role holds no such grant.
    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    #: **A stamped id, not a reference** -- exactly what `audit_log` does with
    #: `actor_user_id`, and for the same reason stated there: an `owner` may
    #: hard delete a user, `ON DELETE SET NULL` would then issue an `UPDATE` on
    #: this table, and the runtime role holds no UPDATE grant on it. A cascade
    #: would make every hard delete of a person fail with a permission error.
    user_id = models.UUIDField(null=True, blank=True)
    #: The other half of S0's referential rule: **stamp the human-readable
    #: identity at write time**. The lot trace is the INVIMA answer and names the
    #: person who moved each unit; a trace that reads as nobody's after a
    #: roster change is not an answer an inspector takes.
    user_name = models.CharField(max_length=200, blank=True)

    #: A5 · uuid v7 from a till, uuid v5 over the document's own natural key
    #: where the server originated the move. **Never null**, so
    #: `UNIQUE (tenant_id, client_uuid)` is a real guard on every path: pressing
    #: a transfer resolution twice appends nothing, for the same reason a
    #: replayed push does.
    client_uuid = models.UUIDField()

    #: Coined (*Gated on*). The ledger's `adjustment` row requires "a stated
    #: reason" and gives it no column. Constrained text over the vocabulary in
    #: `STOCK_MOVE_REASONS`, so a later stage adds a value without a migration.
    reason = models.CharField(max_length=32, blank=True)
    #: Coined (*Gated on*).
    note = models.TextField(blank=True)
    #: Coined (*Gated on*), and **stamped rather than derived**: whether the
    #: chosen lot was the FEFO head is unrecoverable once the projection moves
    #: on, which is the argument the ledger already makes for
    #: `sale_lines.unit_cost`.
    fefo_override = models.BooleanField(default=False)

    class Meta:
        db_table = "stock_moves"
        ordering = ["-recorded_at"]
        indexes = [
            # Rule 4 · three read paths, all S3's own on S3's own table. The
            # second is named in the ledger as created by S3 and inherited by
            # S6 and S9.
            models.Index(
                fields=["tenant", "lot", "recorded_at"], name="stock_moves_lot_trace"
            ),
            models.Index(
                fields=["tenant", "location", "item", "recorded_at"],
                name="stock_moves_item_history",
            ),
            models.Index(
                fields=["tenant", "location", "recorded_at"],
                name="stock_moves_location_scan",
            ),
        ]
        constraints = [
            # A5, rule 8. The whole of deduplication is this index.
            models.UniqueConstraint(
                fields=["tenant", "client_uuid"], name="one_move_per_client_uuid"
            ),
            models.CheckConstraint(
                condition=~models.Q(quantity=0), name="a_move_moves_something"
            ),
            # The sign of each type, in the database rather than in a service
            # somebody could route around.
            models.CheckConstraint(
                condition=(
                    ~models.Q(type__in=POSITIVE_MOVE_TYPES) | models.Q(quantity__gt=0)
                )
                & (~models.Q(type__in=NEGATIVE_MOVE_TYPES) | models.Q(quantity__lt=0)),
                name="a_move_carries_the_sign_of_its_type",
            ),
            # A reason is required with the four reconciling types and refused
            # with every other one.
            models.CheckConstraint(
                condition=(
                    models.Q(type__in=REASONED_MOVE_TYPES) & ~models.Q(reason="")
                )
                | (~models.Q(type__in=REASONED_MOVE_TYPES) & models.Q(reason="")),
                name="a_reason_belongs_to_a_reconciling_move",
            ),
            models.CheckConstraint(
                condition=models.Q(reason="") | models.Q(reason__in=STOCK_MOVE_REASONS),
                name="move_reason_is_declared",
            ),
        ]

    def __str__(self):
        return f"{self.type} {self.quantity}"


class StockOnHand(TenantScopedModel):
    """The projection. **Derived, rebuildable, never the source of truth** (A3).

    It exists because summing a ledger on every grid page is not a 400ms query
    (§4), and for no other reason. It is written by `core.inventory.ledger` and
    by nothing else, it is never read as truth in an argument about what
    happened, and the rebuild is the standing proof that dropping it costs
    nothing.
    """

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(
        Lot, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    #: Signed: §5 rule 2 lets a sale drive it below zero rather than refusing at
    #: a counter, and the negative is an exception raised to the office.
    quantity = models.IntegerField(default=0)

    class Meta:
        db_table = "stock_on_hand"
        ordering = ["location__name", "item__name"]
        indexes = [
            # Rule 4 · S2's delta cursor, in the ledger's own four-column shape.
            models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="stock_on_hand_delta_cursor",
            ),
            # Rule 4 · **the second delta cursor, and this table is the one
            # place in the product that needs two.** It serves the
            # `stock_elsewhere` collection, whose scan is not location-keyed at
            # all: it is every *other* sede's rows for the items this one is in
            # trouble on, so the index it ranges over is the tenant-wide shape
            # and the item set is a residual applied per row. One index per
            # collection, and both are cursor shapes rather than a compromise
            # that serves neither.
            models.Index(
                fields=["tenant", "updated_at", "id"],
                name="stock_on_hand_tenant_cursor",
            ),
            # The availability query behind `hay N en <sede>` and behind S4's
            # counter lookup: one item, every location.
            models.Index(
                fields=["tenant", "item", "location"], name="stock_on_hand_availability"
            ),
        ]
        constraints = [
            # **NULLS NOT DISTINCT.** Without it a lot-less item silently
            # accumulates one projection row per write, and every figure on the
            # screen is the last one written rather than the sum.
            models.UniqueConstraint(
                fields=["tenant", "location", "item", "lot"],
                name="one_projection_row_per_key",
                nulls_distinct=False,
            ),
        ]

    def __str__(self):
        return f"{self.item_id} @ {self.location_id}: {self.quantity}"


class StockPolicy(TenantScopedModel):
    """The thresholds the `Estado` derivation reads before a forecast exists.

    A location-specific row wins over a network-wide one for the same item.
    **S3 writes `manual` and S6 writes `model`** (ledger); a write over a
    `model` row flips `source` back, which is the point of the column.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="policies")
    #: Null means network-wide.
    location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    min_quantity = models.IntegerField(null=True, blank=True)
    max_quantity = models.IntegerField(null=True, blank=True)
    reorder_point = models.IntegerField(null=True, blank=True)
    target_coverage_days = models.IntegerField(null=True, blank=True)
    source = models.CharField(
        max_length=16, choices=PolicySource, default=PolicySource.MANUAL
    )

    class Meta:
        db_table = "stock_policies"
        ordering = ["item__name"]
        indexes = [
            models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="stock_policies_delta_cursor",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "item", "location"],
                name="one_policy_per_item_and_scope",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=PolicySource.values),
                name="policy_source_is_declared",
            ),
        ]

    def __str__(self):
        return f"{self.item_id} · {self.source}"


class Transfer(TenantScopedModel):
    """Merchandise moving between two sedes, as a document with two ends and two
    moments (§3).

    Between dispatch and receipt the units are on no shelf and are reported as
    **En tránsito** -- a figure on the transfer, never a state in the `Estado`
    column, because a box in a van belongs to neither sede's shelf and
    pretending otherwise is how transfers lose merchandise.
    """

    #: Per tenant, sequential, allocated server-side.
    number = models.PositiveIntegerField()
    origin_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name="+"
    )
    destination_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(
        max_length=16, choices=TransferStatus, default=TransferStatus.DRAFT
    )
    dispatched_at = models.DateTimeField(null=True, blank=True)
    dispatched_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    dispatched_by_name = models.CharField(max_length=200, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    received_by_name = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        db_table = "transfers"
        ordering = ["-number"]
        indexes = [
            models.Index(
                fields=["tenant", "status", "-number"], name="transfers_work_list"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "number"], name="one_transfer_number_per_tenant"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=TransferStatus.values),
                name="transfer_status_is_declared",
            ),
            # A transfer to the sede it left is not a transfer.
            models.CheckConstraint(
                condition=~models.Q(origin_location=models.F("destination_location")),
                name="a_transfer_has_two_ends",
            ),
        ]

    def __str__(self):
        return f"Traslado {self.number}"


class TransferLine(TenantScopedModel):
    transfer = models.ForeignKey(
        Transfer, on_delete=models.CASCADE, related_name="lines"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(
        Lot, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    quantity_requested = models.PositiveIntegerField(default=0)
    quantity_dispatched = models.PositiveIntegerField(default=0)
    quantity_received = models.PositiveIntegerField(default=0)
    #: Null until a shortfall is closed. `''` is not used here: this is a state
    #: with three values and a stated absence, not an identifier.
    resolution = models.CharField(max_length=16, choices=TransferResolution, blank=True)

    class Meta:
        db_table = "transfer_lines"
        ordering = ["item__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "transfer", "item", "lot"],
                name="one_transfer_line_per_item_and_lot",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=models.Q(resolution="")
                | models.Q(resolution__in=TransferResolution.values),
                name="transfer_resolution_is_declared",
            ),
        ]

    def __str__(self):
        return f"{self.item_id} × {self.quantity_requested}"


class StockCount(TenantScopedModel):
    """A cycle count: the shelf reconciled to the record by writing the
    difference down, not by erasing it (§6).

    It carries the client-write quartet because the counting surface is walked
    around a back room where the wifi is worst (ledger rule 8).
    """

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    scope = models.CharField(max_length=16, choices=CountScope, default=CountScope.FULL)
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(
        max_length=16, choices=CountStatus, default=CountStatus.COUNTING
    )
    counted_by_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    counted_by_name = models.CharField(max_length=200, blank=True)
    closed_by_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    closed_by_name = models.CharField(max_length=200, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    client_uuid = models.UUIDField()
    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "stock_counts"
        ordering = ["-recorded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "client_uuid"], name="one_count_per_client_uuid"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=CountStatus.values),
                name="count_status_is_declared",
            ),
            models.CheckConstraint(
                condition=models.Q(scope__in=CountScope.values),
                name="count_scope_is_declared",
            ),
            # A count scoped to a category with no category is a count over
            # everything wearing a narrower label.
            models.CheckConstraint(
                condition=~models.Q(scope=CountScope.CATEGORY)
                | models.Q(category__isnull=False),
                name="a_category_count_names_a_category",
            ),
        ]

    def __str__(self):
        return f"Conteo {self.location_id} · {self.status}"


class StockCountLine(TenantScopedModel):
    """One counted line. `expected_quantity` is stamped **when the line is
    entered**, not at close.

    That is the whole arithmetic of a count: the adjusting move is the
    discrepancy measured at entry, and every sale made during the count applies
    on top of it. Stamping at close instead double-counts them.
    """

    count = models.ForeignKey(
        StockCount, on_delete=models.CASCADE, related_name="lines"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(
        Lot, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    expected_quantity = models.IntegerField(default=0)
    counted_quantity = models.IntegerField(default=0)
    entered_at = models.DateTimeField(default=timezone.now)

    client_uuid = models.UUIDField()
    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "stock_count_lines"
        ordering = ["item__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "client_uuid"], name="one_count_line_per_client_uuid"
            ),
            models.UniqueConstraint(
                fields=["tenant", "count", "item", "lot"],
                name="one_count_line_per_item_and_lot",
                nulls_distinct=False,
            ),
        ]

    def __str__(self):
        return f"{self.item_id}: {self.counted_quantity}"


# ---------------------------------------------------------------------------
# S4 · the counter. Six tables and four enums (ledger, architecture §3, §6).
#
# **Nothing here is written by an endpoint.** Every sale, line, payment, shift
# and return originates on a device and arrives through S2's
# `POST /api/sync/push` (A5, §5 rule 5); the office's only mutations are a void
# and a forced close, each of which moves a row that a till already wrote. A
# second write path would be a second way to allocate `sales.number` and a
# second way to move stock, and rule 7 has exactly one of the latter.
#
# **Money is stored tax-inclusive.** A Colombian shelf price includes IVA and
# the handoff's own ticket confirms it -- `Subtotal $15.600 · Descuento $0 ·
# Total $15.600`, with no tax line and no tax added to the total. So
# `sale_lines.unit_price` is what the customer pays per base unit, `tax_amount`
# is the IVA *contained* in the line, and `sales.tax` is inside `sales.total`
# and never added to it. The arithmetic lives in `core.counter.money` and is
# checked by a CHECK constraint below, because a build that added `tax` to
# `total` would produce a ticket 19% too expensive on cosmetics and exactly
# right on medicine -- the kind of defect a pilot finds three weeks in.
#
# **Quantities are base units, always** -- the same rule `stock_moves` follows
# (§3). A `splittable` item's pack is `units_per_pack` base units and not a
# second unit of measure, so nothing about a line, a move or the document handed
# to an invoicing system distinguishes a box of twenty from twenty singles.
#
# Every table below carries the same S0 convention as the twenty-two above and
# the client-write quartet rule 8 names; the policies live in migration 0012.
# ---------------------------------------------------------------------------


class ShiftStatus(models.TextChoices):
    """**Coined** (S4, *Gated on*). §3 gives `shifts.status` a column and the
    ledger's enum register gives it no name and no values.

    Two, and there is no third. A forced close is a `closed` shift with a null
    `declared_total` -- not a state of its own -- because a forced close is the
    absence of a count and not a different kind of session, and a third value
    would put that distinction in two places at once.
    """

    OPEN = "open", "Abierto"
    CLOSED = "closed", "Cerrado"


class SaleStatus(models.TextChoices):
    """`open` is a ticket in progress and is what makes a crash mid-ticket cost
    nothing: the row is written locally the moment its first line lands, and the
    cashier finds the ticket where they left it (§5)."""

    OPEN = "open", "Abierta"
    CLOSED = "closed", "Cerrada"
    VOIDED = "voided", "Anulada"


class SaleSource(models.TextChoices):
    """**S4 writes `counter`, S6 writes `imported`** (ledger, disputed columns).

    An imported sale was rung up and invoiced in the client's previous system
    long before Botica existed, so it must never appear in a shift, in a cash
    reconciliation or in a fiscal handoff -- handing a month of history to an
    invoicing system is a month of duplicate invoices (§8). The `CHECK` that
    enforces the first of those is created here.
    """

    COUNTER = "counter", "Mostrador"
    IMPORTED = "imported", "Historial cargado"


class PaymentMethod(models.TextChoices):
    """The name is coined (S4, *Gated on*); the five values are §3's verbatim."""

    CASH = "cash", "Efectivo"
    DEBIT_CARD = "debit_card", "Débito"
    CREDIT_CARD = "credit_card", "Crédito"
    TRANSFER = "transfer", "Transferencia"
    OTHER = "other", "Otro"


class ClientWrittenModel(TenantScopedModel):
    """Rule 8's quartet, declared once for the six tables that carry it.

    `client_uuid` is the till's own uuid v7 and `UNIQUE (tenant_id,
    client_uuid)` -- declared per table, because a constraint needs a name --
    is the whole of deduplication (A5). `occurred_at` is the device's clock,
    stored exactly as it sent it and **never corrected** (§5 rule 4);
    `recorded_at` is the server's, and it is the column every report and every
    rollup reads.

    `device` is `SET_NULL` rather than `PROTECT`, which is the opposite of the
    choice `stock_moves` made: these tables are not append-only at the grant
    level -- a sale is closed and voided in place -- so an `UPDATE` on the way
    out is a statement the runtime role actually holds.

    S3's two till-written tables declare the same four columns inline. They are
    not moved onto this base: a migration that re-parents an existing table's
    fields for tidiness is a migration with nothing to gain and a state to get
    wrong.
    """

    client_uuid = models.UUIDField()
    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class Shift(ClientWrittenModel):
    """A **turno**: open with a float, sell, close with a declared count.

    **`variance` is stored whether it is positive, negative or zero** (ledger,
    disputed columns). A till that quietly reconciles its own shortfalls is a
    till nobody can audit, and the whole reason a droguería's owner buys this
    product is that today nobody can audit theirs (§6).

    The drawer belongs to the **till**, not to the person: one open turno per
    device, opened by whoever was signed in, sold into by whoever is signed in
    at the time. `sales.sold_by_user_id` records who rang each ticket.
    """

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    #: Who opened it. S0's referential rule: `SET_NULL` plus the stamped name,
    #: so a shift whose cashier was later hard-deleted still says who counted
    #: the drawer.
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    user_name = models.CharField(max_length=200, blank=True)

    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    opening_float = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    #: **Null only on a forced close**, which is the absence of a count rather
    #: than a count of nothing. Zero is a real declared total and means the
    #: drawer was emptied.
    declared_total = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    variance = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    status = EnumField(
        max_length=16,
        choices=ShiftStatus,
        db_enum="shift_status",
        default=ShiftStatus.OPEN,
    )
    #: Why a turno was force-closed, stamped from the endpoint's own reason so
    #: the Turnos list can say `Cierre forzado` without reading `audit_log`.
    forced_close_reason = models.TextField(blank=True)

    class Meta:
        db_table = "shifts"
        ordering = ["-opened_at"]
        indexes = [
            # Rule 4 · S2's delta cursor, in the ledger's own four-column shape.
            models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="shifts_delta_cursor",
            ),
            models.Index(
                fields=["tenant", "location", "-opened_at"], name="shifts_office_list"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "client_uuid"], name="one_shift_per_client_uuid"
            ),
            # **One open turno per device.** Partial, so a device accumulates
            # any number of closed shifts and never a second open one -- which
            # is what makes `sales.shift_id` unambiguous on a till that has been
            # selling for a year.
            models.UniqueConstraint(
                fields=["tenant", "device"],
                condition=models.Q(status=ShiftStatus.OPEN),
                name="one_open_shift_per_device",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=ShiftStatus.values),
                name="shift_status_is_declared",
            ),
            # An open turno has no close on it, and a closed one has one. A
            # `closed_at` on an open shift is a row two screens would read
            # differently.
            models.CheckConstraint(
                condition=(
                    models.Q(status=ShiftStatus.OPEN, closed_at__isnull=True)
                    | models.Q(status=ShiftStatus.CLOSED, closed_at__isnull=False)
                ),
                name="a_closed_shift_has_a_close",
            ),
            # A count and its difference stand or fall together: `variance` is
            # `declared_total - expected`, so a declared total with no variance
            # beside it is an arithmetic nobody ran.
            models.CheckConstraint(
                condition=(
                    models.Q(declared_total__isnull=True, variance__isnull=True)
                    | models.Q(declared_total__isnull=False, variance__isnull=False)
                ),
                name="a_declared_total_carries_its_variance",
            ),
        ]

    def __str__(self):
        return f"{self.location_id} · {self.opened_at:%Y-%m-%d %H:%M}"


class Sale(ClientWrittenModel):
    """One ticket.

    **`number` is the internal, per-location sale number and is not a fiscal
    number** (ledger, disputed columns). Botica allocates no fiscal number at
    v1 -- numbering leases and `dian_resolutions` are not built (A6) -- so the
    fiscal number is whatever the receiving system issued, and `sales.number` is
    the key both systems reconcile on (§8). It is composed
    `{device code}-{per-device sequence}`, because the number must be
    allocatable on a till with no connection and must never collide across two
    tills in one sede, and the only mechanism that gives a bare integer that
    property is a central allocator or a lease -- neither of which exists.

    `customer` is `PROTECT` and is **neither `CASCADE` nor `SET_NULL`**: a
    cascade deletes the sales when an administrator presses a legally required
    button, and a `SET_NULL` keeps them and loses the acquirer S5 has to name on
    the canonical document. S1's Ley 1581 deletion erases the identifying fields
    in place and never deletes a referenced row, so this constraint is never
    reached -- which is exactly what makes it the right one.
    """

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    #: Null on an `imported` sale and never on a counter one -- the CHECK below
    #: is what makes that structural.
    shift = models.ForeignKey(
        Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="sales"
    )
    number = models.CharField(max_length=32)
    status = EnumField(
        max_length=16,
        choices=SaleStatus,
        db_enum="sale_status",
        default=SaleStatus.OPEN,
    )
    source = EnumField(
        max_length=16,
        choices=SaleSource,
        db_enum="sale_source",
        default=SaleSource.COUNTER,
    )
    customer = models.ForeignKey(
        Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="sales"
    )

    #: The sum of line gross amounts **before** discount.
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    #: The IVA **contained** in `total`, never added to it.
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    sold_by_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    #: S0's referential rule again: stamp the readable identity at write time,
    #: so a receipt reprinted after a roster change still names the cashier.
    sold_by_name = models.CharField(max_length=200, blank=True)

    closed_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)

    class Meta:
        db_table = "sales"
        ordering = ["-recorded_at"]
        indexes = [
            # Rule 4 · S2's delta cursor.
            models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="sales_delta_cursor",
            ),
            # Rule 4 · **named in the ledger as created by S4 and inherited by
            # S9.** S4's office list -- sede plus period, the default view of
            # `GET /api/sales` -- is the first read path that needs it, and an
            # index is created once by the first stage that needs it.
            models.Index(
                fields=["tenant", "location", "recorded_at"],
                name="sales_location_period",
            ),
            # The close report and the shift record panel.
            models.Index(fields=["tenant", "shift"], name="sales_by_shift"),
            # Rule 4 · **`Mostrador 3`.** The office's nav counter is a count of
            # open counter sales, polled every thirty seconds by every office
            # session; over the largest table in the product that is a scan of
            # one sede's whole history for a number that is almost always
            # single-digit. Partial, because the rows it counts are a vanishing
            # share of the table and an index over the rest would be paid for on
            # every ticket.
            models.Index(
                fields=["tenant", "location"],
                condition=models.Q(status="open", source="counter"),
                name="sales_open_at_the_counter",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "client_uuid"], name="one_sale_per_client_uuid"
            ),
            models.UniqueConstraint(
                fields=["tenant", "location", "number"],
                name="one_sale_number_per_location",
            ),
            # **A counter sale outside a turno cannot be reconciled, and an
            # imported sale must never appear in one** (ledger).
            models.CheckConstraint(
                condition=~models.Q(source=SaleSource.COUNTER)
                | models.Q(shift__isnull=False),
                name="a_counter_sale_sits_in_a_shift",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=SaleStatus.values),
                name="sale_status_is_declared",
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=SaleSource.values),
                name="sale_source_is_declared",
            ),
            # The money composition, in the database rather than in a service
            # somebody could route around. `tax` is inside `total`, so it is
            # bounded by it rather than added to it.
            models.CheckConstraint(
                condition=models.Q(total=models.F("subtotal") - models.F("discount")),
                name="a_total_is_its_subtotal_less_its_discount",
            ),
            models.CheckConstraint(
                condition=models.Q(tax__lte=models.F("total")),
                name="tax_is_contained_in_the_total",
            ),
        ]

    def __str__(self):
        return self.number


class SaleLine(ClientWrittenModel):
    """One line of a ticket.

    **`unit_cost` and `vat_class` are stamped at the moment of sale** (ledger,
    disputed columns). Margin joined later to a lot's *current* cost is wrong
    the first time a cost changes, and a tax class edited next month must not
    restate what was charged today. Every margin figure on S9's Panel rests on
    the first of those being stamped rather than derived.

    `position` is **coined** (S4, *Gated on*): a ticket's line order is what the
    cashier reads and what the receipt prints, and replicated rows arrive in no
    order at all.

    `location_id` is **denormalised from the parent**, and so is it on
    `payments` and `sale_return_lines`. S2's delta cursor index is
    `(tenant_id, location_id, updated_at, id)` on every synced table (rule 4); a
    child that had to join its parent to answer the replication predicate would
    turn a cursor scan into a per-row join on every pull, on the one query §4
    budgets at under 20ms.
    """

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    position = models.PositiveIntegerField()
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    #: Null for a service and for an item whose `tracks_lots` is false. FEFO by
    #: default; a cashier's override is recorded here **and** on the move S3's
    #: service appends, which carries `fefo_override` (§6).
    lot = models.ForeignKey(
        Lot, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    #: Base units, always. Positive: a line of nothing is not a line, and a
    #: negative line is a return, which has its own table.
    quantity = models.IntegerField()
    #: What the customer pays per base unit, **IVA included**.
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    vat_class = EnumField(max_length=16, choices=VatClass, db_enum="vat_class")
    #: The IVA contained in this line's net amount.
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    #: Stamped from the lot, or from `items.service_cost` where there is no lot.
    #: Null means no cost was recorded, **never zero** (§B.9.2 tier 3).
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    #: Created here, defaulted false and **never written by this stage**
    #: (ledger, disputed columns). S8 writes it when `Agregar` is pressed on a
    #: suggestion card, and it is the single column that makes the Panel's
    #: `58,6% de sugerencias aceptadas` answerable.
    from_suggestion = models.BooleanField(default=False)

    class Meta:
        db_table = "sale_lines"
        ordering = ["position"]
        indexes = [
            models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="sale_lines_delta_cursor",
            ),
            models.Index(fields=["tenant", "sale"], name="sale_lines_by_sale"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "client_uuid"], name="one_sale_line_per_client_uuid"
            ),
            models.UniqueConstraint(
                fields=["tenant", "sale", "position"],
                name="one_line_per_position_per_sale",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="a_sale_line_sells_something"
            ),
            models.CheckConstraint(
                condition=models.Q(vat_class__in=VatClass.values),
                name="sale_line_vat_class_is_declared",
            ),
        ]

    def __str__(self):
        return f"{self.item_id} × {self.quantity}"


class Payment(ClientWrittenModel):
    """One method applied to one sale.

    **`amount` is what was applied to the sale, not what was tendered.** Cash
    tendered and the change given back are display figures on the receipt and
    are not stored: the sale was paid for with `total`, however many notes
    crossed the counter.
    """

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    method = EnumField(max_length=16, choices=PaymentMethod, db_enum="payment_method")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    #: The voucher or transfer reference, for the three methods that have one.
    reference = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "payments"
        ordering = ["method"]
        indexes = [
            models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="payments_delta_cursor",
            ),
            models.Index(fields=["tenant", "sale"], name="payments_by_sale"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "client_uuid"], name="one_payment_per_client_uuid"
            ),
            models.CheckConstraint(
                condition=models.Q(method__in=PaymentMethod.values),
                name="payment_method_is_declared",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="a_payment_pays_something"
            ),
        ]

    def __str__(self):
        return f"{self.method} {self.amount}"


class SaleReturn(ClientWrittenModel):
    """A **devolución** against a closed sale, whole or partial.

    §3 fixes the behaviour and enumerates no columns; these are coined (S4,
    *Gated on*). The sale it reverses stays `closed` -- a fully-returned sale is
    a closed sale with returns against it, not a voided one -- and the credit
    note a return legally requires is issued by the client's own invoicing
    system, never by Botica (§8).
    """

    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="returns")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    #: The turno the **refund** came out of, which is not the turno the sale was
    #: rung in: money leaves the drawer that is open now.
    shift = models.ForeignKey(
        Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="returns"
    )
    number = models.CharField(max_length=32)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reason = models.TextField()
    refund_method = EnumField(
        max_length=16, choices=PaymentMethod, db_enum="payment_method"
    )
    returned_by_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    returned_by_name = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "sale_returns"
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="sale_returns_delta_cursor",
            ),
            models.Index(fields=["tenant", "sale"], name="sale_returns_by_sale"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "client_uuid"], name="one_return_per_client_uuid"
            ),
            models.UniqueConstraint(
                fields=["tenant", "location", "number"],
                name="one_return_number_per_location",
            ),
            models.CheckConstraint(
                condition=models.Q(refund_method__in=PaymentMethod.values),
                name="refund_method_is_declared",
            ),
            models.CheckConstraint(
                condition=~models.Q(reason=""), name="a_return_states_its_reason"
            ),
        ]

    def __str__(self):
        return self.number


class SaleReturnLine(ClientWrittenModel):
    """One line of a return.

    **Its money is stamped from the original sale line**, not from today's price
    list: a credit note must reverse what was charged, and a price that changed
    in between is exactly the case §5 says the sale's own record settles. The
    stock goes back **to the lot the line originally sold**, or a recall becomes
    unanswerable (§6).
    """

    sale_return = models.ForeignKey(
        SaleReturn, on_delete=models.CASCADE, related_name="lines"
    )
    sale_line = models.ForeignKey(SaleLine, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey(
        Lot, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    vat_class = EnumField(max_length=16, choices=VatClass, db_enum="vat_class")
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    class Meta:
        db_table = "sale_return_lines"
        ordering = ["id"]
        indexes = [
            models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="return_lines_delta_cursor",
            ),
            # The ledger's rule reads `(tenant_id, sale_id)` on each child; this
            # table's parent id is the return's, and it is the one this table's
            # own read path -- what remains returnable on a line -- ranges over.
            models.Index(
                fields=["tenant", "sale_return"], name="return_lines_by_return"
            ),
            # What remains returnable **per original line**, which is the figure
            # the devolución's stepper is capped at.
            models.Index(fields=["tenant", "sale_line"], name="return_lines_by_line"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "client_uuid"],
                name="one_return_line_per_client_uuid",
            ),
            models.UniqueConstraint(
                fields=["tenant", "sale_return", "sale_line"],
                name="one_return_line_per_original_line",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="a_return_line_returns_something",
            ),
            models.CheckConstraint(
                condition=models.Q(vat_class__in=VatClass.values),
                name="return_line_vat_class_is_declared",
            ),
        ]

    def __str__(self):
        return f"{self.item_id} × {self.quantity}"
