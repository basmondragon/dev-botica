"""S0's five tables and the two enums the ledger names.

The RLS policies, the grants and the resolution function live in 0002 rather
than here, so that the security layer is one file somebody can read in SQL.
"""

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Tenant",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=200)),
                ("slug", models.SlugField(max_length=100, unique=True)),
                ("nit", models.CharField(blank=True, max_length=32)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Activa"), ("suspended", "Suspendida")],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("settings", models.JSONField(blank=True, default=dict)),
            ],
            options={"db_table": "tenants", "ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Location",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(max_length=32)),
                ("name", models.CharField(max_length=200)),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("store", "Punto de venta"),
                            ("warehouse", "Bodega"),
                            ("distribution_center", "Centro de distribución"),
                        ],
                        default="store",
                        max_length=32,
                    ),
                ),
                ("address", models.CharField(blank=True, max_length=300)),
                ("city", models.CharField(blank=True, max_length=120)),
                ("phone", models.CharField(blank=True, max_length=40)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Activa"), ("closed", "Cerrada")],
                        default="active",
                        max_length=20,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="core.tenant",
                    ),
                ),
            ],
            options={"db_table": "locations", "ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="User",
            fields=[
                ("password", models.CharField(max_length=128, verbose_name="password")),
                (
                    "last_login",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="last login"
                    ),
                ),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("email", models.EmailField(max_length=254)),
                ("name", models.CharField(max_length=200)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("platform_admin", "Plataforma"),
                            ("owner", "Propietaria"),
                            ("admin", "Administradora"),
                            ("cashier", "Mostrador"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Activo"), ("suspended", "Suspendido")],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("platform_admin", models.BooleanField(default=False)),
                ("is_staff", models.BooleanField(default=False)),
                ("is_superuser", models.BooleanField(default=False)),
                (
                    "location",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="users",
                        to="core.location",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="users",
                        to="core.tenant",
                    ),
                ),
            ],
            options={"db_table": "users", "ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Invitation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("email", models.EmailField(max_length=254)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("platform_admin", "Plataforma"),
                            ("owner", "Propietaria"),
                            ("admin", "Administradora"),
                            ("cashier", "Mostrador"),
                        ],
                        max_length=20,
                    ),
                ),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("expires_at", models.DateTimeField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pendiente"),
                            ("accepted", "Aceptada"),
                            ("revoked", "Revocada"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("last_delivery_error", models.TextField(blank=True)),
                (
                    "invited_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="invitations_sent",
                        to="core.user",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="invitations",
                        to="core.location",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="core.tenant",
                    ),
                ),
            ],
            options={"db_table": "invitations", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, editable=False
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("actor_email", models.EmailField(max_length=254)),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("create", "Creó"),
                            ("update", "Modificó"),
                            ("delete", "Eliminó"),
                            ("archive", "Archivó"),
                            ("approve", "Aprobó"),
                            ("reject", "Rechazó"),
                            ("send", "Envió"),
                            ("revoke", "Revocó"),
                            ("impersonate", "Entró como"),
                        ],
                        max_length=32,
                    ),
                ),
                ("entity_type", models.CharField(max_length=64)),
                ("entity_id", models.UUIDField(blank=True, null=True)),
                ("before", models.JSONField(blank=True, null=True)),
                ("after", models.JSONField(blank=True, null=True)),
                ("request_id", models.CharField(blank=True, max_length=64)),
                (
                    "actor_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.user",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="core.tenant",
                    ),
                ),
            ],
            options={"db_table": "audit_log", "ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="location",
            constraint=models.UniqueConstraint(
                fields=("tenant", "code"), name="one_location_code_per_tenant"
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                fields=("tenant", "email"), name="one_email_per_tenant"
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("role", "platform_admin"), ("tenant__isnull", True)
                )
                | (
                    models.Q(("role", "platform_admin"), _negated=True)
                    & models.Q(("tenant__isnull", False))
                ),
                name="platform_admin_has_no_tenant",
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(("role", "cashier"), _negated=True)
                | models.Q(("location__isnull", False)),
                name="cashier_has_a_home_location",
            ),
        ),
        migrations.AddConstraint(
            model_name="invitation",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("tenant", "email"),
                name="one_pending_invitation_per_email_per_tenant",
            ),
        ),
        migrations.AddConstraint(
            model_name="invitation",
            constraint=models.CheckConstraint(
                condition=models.Q(("role", "cashier"), _negated=True)
                | models.Q(("location__isnull", False)),
                name="invited_cashier_has_a_home_location",
            ),
        ),
        migrations.AddConstraint(
            model_name="invitation",
            constraint=models.CheckConstraint(
                condition=models.Q(("role__in", ["owner", "admin"]), _negated=True)
                | models.Q(("location__isnull", True)),
                name="invited_office_role_has_no_home_location",
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["tenant", "-created_at"], name="audit_log_tenant_recent"
            ),
        ),
    ]
