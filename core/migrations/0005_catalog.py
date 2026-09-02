"""S1's four enum types, one immutable helper, and its nine catalog tables.

The types come first, because the tables declare them: `items.type` is an
`item_type` column and not a varchar that happens to hold one of two words, and
the same goes for `vat_class`, `invima_status` and `price_source`. The threat
`choices` does not stop is "a management command or a bad backfill", which goes
through no serializer -- and `price_source` holding exactly `manual` and
`imported` with no third value is what makes A11 a property of the schema.

`app_unaccent` comes first for the same reason: `items.search_name` is a
generated column over it, and Postgres's own `unaccent(text)` is not IMMUTABLE
-- it resolves the dictionary at call time -- so it can appear in neither a
generated column nor an index. Naming the dictionary explicitly makes the
two-argument form immutable, and this wrapper is the only thing either of those
two may call.

The RLS policies, the trigram indexes and the two-level category trigger live in
0006, so that the security and search layers stay one file somebody can read in
SQL -- the same split S0 took between 0001 and 0002.
"""

import core.models
import django.db.models.deletion
import django.db.models.functions.text
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


ENUMS = r"""
CREATE TYPE item_type     AS ENUM ('product', 'service');
CREATE TYPE vat_class     AS ENUM ('excluded', 'exempt', 'rate_5', 'rate_19');
CREATE TYPE invima_status AS ENUM ('valid', 'in_process', 'expired',
                                   'not_applicable');
-- Two values, and there is no third (A11).
CREATE TYPE price_source  AS ENUM ('manual', 'imported');

CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE FUNCTION app_unaccent(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
    SELECT unaccent('unaccent', $1)
$$;
"""

ENUMS_REVERSE = r"""
DROP FUNCTION IF EXISTS app_unaccent(text);

DROP TYPE price_source;
DROP TYPE invima_status;
DROP TYPE vat_class;
DROP TYPE item_type;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_declared_values"),
    ]

    operations = [
        migrations.RunSQL(sql=ENUMS, reverse_sql=ENUMS_REVERSE),
        migrations.CreateModel(
            name="Category",
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
                ("name", models.CharField(max_length=120)),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="children",
                        to="core.category",
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
            options={
                "db_table": "categories",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Customer",
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
                ("document_type", models.CharField(blank=True, max_length=8)),
                ("document", models.CharField(blank=True, max_length=32)),
                ("name", models.CharField(blank=True, max_length=200)),
                ("phone", models.CharField(blank=True, max_length=40)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("address", models.CharField(blank=True, max_length=300)),
                ("data_consent", models.BooleanField(default=False)),
                ("data_consent_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "customers",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="ImportRun",
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
                ("kind", models.CharField(max_length=32)),
                ("source", models.CharField(blank=True, max_length=300)),
                ("status", models.CharField(default="running", max_length=16)),
                ("dry_run", models.BooleanField(default=True)),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("rows_read", models.PositiveIntegerField(default=0)),
                ("rows_created", models.PositiveIntegerField(default=0)),
                ("rows_updated", models.PositiveIntegerField(default=0)),
                ("rows_failed", models.PositiveIntegerField(default=0)),
                ("errors", models.JSONField(blank=True, default=list)),
                (
                    "started_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
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
            options={
                "db_table": "imports",
                "ordering": ["-started_at"],
            },
        ),
        migrations.CreateModel(
            name="Item",
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
                (
                    "type",
                    core.models.EnumField(
                        choices=[("product", "Producto"), ("service", "Servicio")],
                        db_enum="item_type",
                        max_length=16,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("presentation", models.CharField(blank=True, max_length=200)),
                ("active_ingredient", models.CharField(blank=True, max_length=200)),
                ("strength", models.CharField(blank=True, max_length=80)),
                ("invima_registration", models.CharField(blank=True, max_length=64)),
                ("invima_expires_at", models.DateField(blank=True, null=True)),
                (
                    "invima_status",
                    core.models.EnumField(
                        choices=[
                            ("valid", "Registro vigente"),
                            ("in_process", "En trámite"),
                            ("expired", "Registro vencido"),
                            ("not_applicable", "No aplica"),
                        ],
                        db_enum="invima_status",
                        max_length=20,
                    ),
                ),
                ("requires_prescription", models.BooleanField(default=False)),
                ("controlled", models.BooleanField(default=False)),
                ("cold_chain", models.BooleanField(default=False)),
                ("unit", models.CharField(max_length=40)),
                ("splittable", models.BooleanField(default=False)),
                ("units_per_pack", models.PositiveIntegerField(default=1)),
                (
                    "vat_class",
                    core.models.EnumField(
                        choices=[
                            ("excluded", "Excluido de IVA"),
                            ("exempt", "Exento de IVA"),
                            ("rate_5", "IVA 5%"),
                            ("rate_19", "IVA 19%"),
                        ],
                        db_enum="vat_class",
                        max_length=16,
                    ),
                ),
                ("tracks_stock", models.BooleanField(default=True)),
                ("tracks_lots", models.BooleanField(default=True)),
                ("tracks_expiry", models.BooleanField(default=True)),
                ("active", models.BooleanField(default=True)),
                ("custom", models.JSONField(blank=True, default=dict)),
                (
                    "regulated_max_price",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                ("cap_status", models.CharField(blank=True, max_length=32)),
                ("external_code", models.CharField(blank=True, max_length=64)),
                (
                    "service_cost",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                (
                    "search_name",
                    models.GeneratedField(
                        db_persist=True,
                        expression=django.db.models.functions.text.Lower(
                            core.models.Unaccented("name")
                        ),
                        output_field=models.TextField(),
                    ),
                ),
                (
                    "category",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="items",
                        to="core.category",
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
            options={
                "db_table": "items",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="ItemBarcode",
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
                ("code", models.CharField(max_length=64)),
                ("is_primary", models.BooleanField(default=False)),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="barcodes",
                        to="core.item",
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
            options={
                "db_table": "item_barcodes",
                "ordering": ["-is_primary", "code"],
            },
        ),
        migrations.CreateModel(
            name="ItemPrice",
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
                ("price", models.DecimalField(decimal_places=2, max_digits=12)),
                ("effective_from", models.DateField()),
                ("effective_to", models.DateField(blank=True, null=True)),
                (
                    "source",
                    core.models.EnumField(
                        choices=[
                            ("manual", "Fijado por una persona"),
                            ("imported", "Cargado desde el sistema anterior"),
                        ],
                        db_enum="price_source",
                        max_length=16,
                    ),
                ),
                ("proposal_id", models.UUIDField(blank=True, null=True)),
                ("set_by_name", models.CharField(blank=True, max_length=200)),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="prices",
                        to="core.item",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.location",
                    ),
                ),
                (
                    "set_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
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
            options={
                "db_table": "item_prices",
                "ordering": ["-effective_from"],
            },
        ),
        migrations.CreateModel(
            name="Manufacturer",
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
                ("nit", models.CharField(blank=True, max_length=32)),
                (
                    "search_name",
                    models.GeneratedField(
                        db_persist=True,
                        expression=django.db.models.functions.text.Lower(
                            core.models.Unaccented("name")
                        ),
                        output_field=models.TextField(),
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
            options={
                "db_table": "manufacturers",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="item",
            name="manufacturer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="items",
                to="core.manufacturer",
            ),
        ),
        migrations.CreateModel(
            name="Supplier",
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
                ("nit", models.CharField(blank=True, max_length=32)),
                ("name", models.CharField(max_length=200)),
                ("contact", models.CharField(blank=True, max_length=200)),
                ("payment_terms", models.CharField(blank=True, max_length=120)),
                (
                    "lead_time_days",
                    models.PositiveSmallIntegerField(blank=True, null=True),
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
            options={
                "db_table": "suppliers",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="SupplierItem",
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
                ("supplier_code", models.CharField(blank=True, max_length=64)),
                (
                    "cost",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                ("min_order_pack", models.PositiveIntegerField(default=1)),
                ("is_preferred", models.BooleanField(default=False)),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="supplier_items",
                        to="core.item",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="supplier_items",
                        to="core.supplier",
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
            options={
                "db_table": "supplier_items",
                "ordering": ["supplier__name"],
            },
        ),
        migrations.AddConstraint(
            model_name="category",
            constraint=models.UniqueConstraint(
                fields=("tenant", "name", "parent"),
                name="one_category_name_per_parent_per_tenant",
                nulls_distinct=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="customer",
            constraint=models.UniqueConstraint(
                condition=models.Q(("document", ""), _negated=True),
                fields=("tenant", "document_type", "document"),
                name="one_customer_per_document_per_tenant",
            ),
        ),
        migrations.AddConstraint(
            model_name="customer",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("document_type", ""),
                    (
                        "document_type__in",
                        ("CC", "CE", "NIT", "TI", "PA", "PEP", "PPT"),
                    ),
                    _connector="OR",
                ),
                name="customer_document_type_is_declared",
            ),
        ),
        migrations.AddConstraint(
            model_name="importrun",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["running", "completed", "failed"])),
                name="import_status_is_declared",
            ),
        ),
        migrations.AddConstraint(
            model_name="itembarcode",
            constraint=models.UniqueConstraint(
                fields=("tenant", "code"), name="one_item_per_barcode_per_tenant"
            ),
        ),
        migrations.AddConstraint(
            model_name="itembarcode",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_primary", True)),
                fields=("tenant", "item"),
                name="one_primary_barcode_per_item",
            ),
        ),
        migrations.AddIndex(
            model_name="itemprice",
            index=models.Index(
                fields=["tenant", "item", "location", "effective_from"],
                name="item_prices_resolution",
            ),
        ),
        migrations.AddConstraint(
            model_name="itemprice",
            constraint=models.UniqueConstraint(
                condition=models.Q(("effective_to__isnull", True)),
                fields=("tenant", "item", "location"),
                name="one_open_price_per_item_and_scope",
                nulls_distinct=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="itemprice",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("effective_to__isnull", True),
                    ("effective_to__gte", models.F("effective_from")),
                    _connector="OR",
                ),
                name="price_window_does_not_close_before_it_opens",
            ),
        ),
        migrations.AddConstraint(
            model_name="manufacturer",
            constraint=models.UniqueConstraint(
                fields=("tenant", "name"), name="one_manufacturer_name_per_tenant"
            ),
        ),
        migrations.AddIndex(
            model_name="item",
            index=models.Index(
                fields=["tenant", "active", "name"], name="items_tenant_active_name"
            ),
        ),
        migrations.AddIndex(
            model_name="item",
            index=models.Index(
                fields=["tenant", "manufacturer"], name="items_tenant_lab"
            ),
        ),
        migrations.AddIndex(
            model_name="item",
            index=models.Index(
                fields=["tenant", "category"], name="items_tenant_category"
            ),
        ),
        migrations.AddIndex(
            model_name="item",
            index=models.Index(
                fields=["tenant", "invima_status"], name="items_tenant_invima_status"
            ),
        ),
        migrations.AddIndex(
            model_name="item",
            index=models.Index(
                fields=["tenant", "invima_expires_at"], name="items_tenant_invima_date"
            ),
        ),
        migrations.AddIndex(
            model_name="item",
            index=models.Index(
                django.db.models.functions.text.Upper("invima_registration"),
                models.F("tenant"),
                name="items_tenant_invima_reg",
            ),
        ),
        migrations.AddConstraint(
            model_name="item",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("tracks_stock", True),
                    models.Q(("tracks_lots", False), ("tracks_expiry", False)),
                    _connector="OR",
                ),
                name="untracked_item_moves_no_lots",
            ),
        ),
        migrations.AddConstraint(
            model_name="item",
            constraint=models.CheckConstraint(
                condition=models.Q(("units_per_pack__gte", 1)),
                name="units_per_pack_is_at_least_one",
            ),
        ),
        migrations.AddConstraint(
            model_name="item",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("splittable", False), ("units_per_pack__gt", 1), _connector="OR"
                ),
                name="splittable_pack_holds_more_than_one",
            ),
        ),
        migrations.AddConstraint(
            model_name="item",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("type", "service"), ("service_cost__isnull", True), _connector="OR"
                ),
                name="only_a_service_carries_a_service_cost",
            ),
        ),
        migrations.AddConstraint(
            model_name="item",
            constraint=models.UniqueConstraint(
                fields=("tenant", "name", "presentation"),
                name="one_item_per_name_and_presentation_per_tenant",
            ),
        ),
        migrations.AddConstraint(
            model_name="item",
            constraint=models.UniqueConstraint(
                condition=models.Q(("external_code", ""), _negated=True),
                fields=("tenant", "external_code"),
                name="one_item_external_code_per_tenant",
            ),
        ),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.UniqueConstraint(
                condition=models.Q(("nit", ""), _negated=True),
                fields=("tenant", "nit"),
                name="one_supplier_nit_per_tenant",
            ),
        ),
        migrations.AddConstraint(
            model_name="supplieritem",
            constraint=models.UniqueConstraint(
                fields=("tenant", "supplier", "item"),
                name="one_link_per_supplier_and_item",
            ),
        ),
        migrations.AddConstraint(
            model_name="supplieritem",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_preferred", True)),
                fields=("tenant", "item"),
                name="one_preferred_supplier_per_item",
            ),
        ),
        migrations.AddConstraint(
            model_name="supplieritem",
            constraint=models.CheckConstraint(
                condition=models.Q(("min_order_pack__gte", 1)),
                name="min_order_pack_is_at_least_one",
            ),
        ),
    ]
