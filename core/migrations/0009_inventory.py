"""S3's one enum type and its eight tables.

**One `CREATE TYPE` and no `ALTER TYPE` anywhere in this stage** (ownership.md,
enum register). `stock_move_type` is created complete, with every value it will
ever hold and the causing stage fixed in the ledger rather than at build time:
S3 causes six of the ten, S4 two and S6 two, and in every case the row is
written by S3's ledger service (rule 7).

`sync_conflict_type` is **not** touched here. S2 declared `negative_stock` at
creation and S3 writes that value; a value migrated by the stage that writes it
would have to land before the stage that reads it, which fails a clean build in
dependency order rather than at the write.

Every other status here -- `stock_policies.source`, `transfers.status`,
`transfer_lines.resolution`, `stock_counts.scope` and `.status` -- is **checked
text and not a Postgres type**, the choice S0 made for the same reason: the
register names exactly one enum for this stage, and a pilot that needs a sixth
transfer state should cost a CHECK change and a label rather than a type
migration.

The RLS policies and the append-only grant live in 0010, so the security layer
stays one file somebody can read in SQL -- the same split S0 took between 0001
and 0002, S1 between 0005 and 0006 and S2 between 0007 and 0008.
"""


import core.models
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


ENUM = r"""
-- Every value, at creation. Six are caused by S3, two by S4 (`sale`,
-- `customer_return`) and two by S6 (`receipt`, `supplier_return`). A row
-- written with the wrong type is not correctable -- `stock_moves` is
-- append-only, so it is only reversible by another row -- which is why the
-- cause of each value is fixed in ownership.md and not chosen here.
CREATE TYPE stock_move_type AS ENUM (
    'receipt',
    'sale',
    'customer_return',
    'supplier_return',
    'transfer_out',
    'transfer_in',
    'adjustment',
    'shrinkage',
    'expiry',
    'count'
);
"""

ENUM_REVERSE = r"""
DROP TYPE stock_move_type;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_sync_rls"),
    ]

    operations = [
        migrations.RunSQL(sql=ENUM, reverse_sql=ENUM_REVERSE),
        migrations.CreateModel(
            name="Lot",
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
                ("lot_code", models.CharField(max_length=64)),
                ("expires_at", models.DateField(blank=True, null=True)),
                (
                    "unit_cost",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                ("invima_registration", models.CharField(blank=True, max_length=64)),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lots",
                        to="core.item",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lots",
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
                "db_table": "lots",
                "ordering": ["expires_at", "lot_code"],
            },
        ),
        migrations.CreateModel(
            name="StockCount",
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
                    "scope",
                    models.CharField(
                        choices=[
                            ("full", "Toda la sede"),
                            ("category", "Una categoría"),
                            ("item_list", "Una lista de productos"),
                        ],
                        default="full",
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Borrador"),
                            ("counting", "En conteo"),
                            ("closed", "Cerrado"),
                        ],
                        default="counting",
                        max_length=16,
                    ),
                ),
                ("counted_by_name", models.CharField(blank=True, max_length=200)),
                ("closed_by_name", models.CharField(blank=True, max_length=200)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("client_uuid", models.UUIDField()),
                (
                    "occurred_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "recorded_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "category",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.category",
                    ),
                ),
                (
                    "closed_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "counted_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "device",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.device",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
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
            options={
                "db_table": "stock_counts",
                "ordering": ["-recorded_at"],
            },
        ),
        migrations.CreateModel(
            name="StockCountLine",
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
                ("expected_quantity", models.IntegerField(default=0)),
                ("counted_quantity", models.IntegerField(default=0)),
                ("entered_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("client_uuid", models.UUIDField()),
                (
                    "occurred_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "recorded_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "count",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="core.stockcount",
                    ),
                ),
                (
                    "device",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.device",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.item",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.lot",
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
                "db_table": "stock_count_lines",
                "ordering": ["item__name"],
            },
        ),
        migrations.CreateModel(
            name="StockMove",
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
                ("quantity", models.IntegerField()),
                (
                    "type",
                    core.models.EnumField(
                        choices=[
                            ("receipt", "Recepción"),
                            ("sale", "Venta"),
                            ("customer_return", "Devolución de cliente"),
                            ("supplier_return", "Devolución a proveedor"),
                            ("transfer_out", "Traslado · salida"),
                            ("transfer_in", "Traslado · entrada"),
                            ("adjustment", "Ajuste"),
                            ("shrinkage", "Merma"),
                            ("expiry", "Vencimiento"),
                            ("count", "Conteo"),
                        ],
                        db_enum="stock_move_type",
                        max_length=24,
                    ),
                ),
                ("document_type", models.CharField(blank=True, max_length=32)),
                ("document_id", models.UUIDField(blank=True, null=True)),
                (
                    "unit_cost",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                (
                    "occurred_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "recorded_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("user_id", models.UUIDField(blank=True, null=True)),
                ("user_name", models.CharField(blank=True, max_length=200)),
                ("client_uuid", models.UUIDField()),
                ("reason", models.CharField(blank=True, max_length=32)),
                ("note", models.TextField(blank=True)),
                ("fefo_override", models.BooleanField(default=False)),
                (
                    "device",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.device",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.item",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.location",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="moves",
                        to="core.lot",
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
                "db_table": "stock_moves",
                "ordering": ["-recorded_at"],
            },
        ),
        migrations.CreateModel(
            name="StockOnHand",
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
                ("quantity", models.IntegerField(default=0)),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.item",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.location",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.lot",
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
                "db_table": "stock_on_hand",
                "ordering": ["location__name", "item__name"],
            },
        ),
        migrations.CreateModel(
            name="StockPolicy",
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
                ("min_quantity", models.IntegerField(blank=True, null=True)),
                ("max_quantity", models.IntegerField(blank=True, null=True)),
                ("reorder_point", models.IntegerField(blank=True, null=True)),
                ("target_coverage_days", models.IntegerField(blank=True, null=True)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("manual", "Fijado por una persona"),
                            ("model", "Calculado por el modelo"),
                        ],
                        default="manual",
                        max_length=16,
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="policies",
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
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "stock_policies",
                "ordering": ["item__name"],
            },
        ),
        migrations.CreateModel(
            name="Transfer",
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
                ("number", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Borrador"),
                            ("dispatched", "Despachado"),
                            ("received", "Recibido"),
                            ("partial", "Recibido parcial"),
                        ],
                        default="draft",
                        max_length=16,
                    ),
                ),
                ("dispatched_at", models.DateTimeField(blank=True, null=True)),
                ("dispatched_by_name", models.CharField(blank=True, max_length=200)),
                ("received_at", models.DateTimeField(blank=True, null=True)),
                ("received_by_name", models.CharField(blank=True, max_length=200)),
                ("note", models.TextField(blank=True)),
                (
                    "destination_location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.location",
                    ),
                ),
                (
                    "dispatched_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "origin_location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.location",
                    ),
                ),
                (
                    "received_by",
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
                "db_table": "transfers",
                "ordering": ["-number"],
            },
        ),
        migrations.CreateModel(
            name="TransferLine",
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
                ("quantity_requested", models.PositiveIntegerField(default=0)),
                ("quantity_dispatched", models.PositiveIntegerField(default=0)),
                ("quantity_received", models.PositiveIntegerField(default=0)),
                (
                    "resolution",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("received_late", "Llegó después"),
                            ("lost_in_transit", "No llegó"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.item",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.lot",
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
                (
                    "transfer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="core.transfer",
                    ),
                ),
            ],
            options={
                "db_table": "transfer_lines",
                "ordering": ["item__name"],
            },
        ),
        migrations.AddIndex(
            model_name="lot",
            index=models.Index(
                fields=["tenant", "updated_at", "id"], name="lots_delta_cursor"
            ),
        ),
        migrations.AddIndex(
            model_name="lot",
            index=models.Index(
                fields=["tenant", "expires_at"], name="lots_tenant_expiry"
            ),
        ),
        migrations.AddIndex(
            model_name="lot",
            index=models.Index(fields=["tenant", "lot_code"], name="lots_tenant_code"),
        ),
        migrations.AddConstraint(
            model_name="lot",
            constraint=models.UniqueConstraint(
                fields=("tenant", "item", "lot_code"),
                name="one_lot_code_per_item_per_tenant",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockcount",
            constraint=models.UniqueConstraint(
                fields=("tenant", "client_uuid"), name="one_count_per_client_uuid"
            ),
        ),
        migrations.AddConstraint(
            model_name="stockcount",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["draft", "counting", "closed"])),
                name="count_status_is_declared",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockcount",
            constraint=models.CheckConstraint(
                condition=models.Q(("scope__in", ["full", "category", "item_list"])),
                name="count_scope_is_declared",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockcount",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("scope", "category"), _negated=True),
                    ("category__isnull", False),
                    _connector="OR",
                ),
                name="a_category_count_names_a_category",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockcountline",
            constraint=models.UniqueConstraint(
                fields=("tenant", "client_uuid"), name="one_count_line_per_client_uuid"
            ),
        ),
        migrations.AddConstraint(
            model_name="stockcountline",
            constraint=models.UniqueConstraint(
                fields=("tenant", "count", "item", "lot"),
                name="one_count_line_per_item_and_lot",
                nulls_distinct=False,
            ),
        ),
        migrations.AddIndex(
            model_name="stockmove",
            index=models.Index(
                fields=["tenant", "lot", "recorded_at"], name="stock_moves_lot_trace"
            ),
        ),
        migrations.AddIndex(
            model_name="stockmove",
            index=models.Index(
                fields=["tenant", "location", "item", "recorded_at"],
                name="stock_moves_item_history",
            ),
        ),
        migrations.AddIndex(
            model_name="stockmove",
            index=models.Index(
                fields=["tenant", "location", "recorded_at"],
                name="stock_moves_location_scan",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockmove",
            constraint=models.UniqueConstraint(
                fields=("tenant", "client_uuid"), name="one_move_per_client_uuid"
            ),
        ),
        migrations.AddConstraint(
            model_name="stockmove",
            constraint=models.CheckConstraint(
                condition=models.Q(("quantity", 0), _negated=True),
                name="a_move_moves_something",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockmove",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        models.Q(
                            ("type__in", ("receipt", "customer_return", "transfer_in")),
                            _negated=True,
                        ),
                        ("quantity__gt", 0),
                        _connector="OR",
                    ),
                    models.Q(
                        models.Q(
                            (
                                "type__in",
                                (
                                    "sale",
                                    "supplier_return",
                                    "transfer_out",
                                    "shrinkage",
                                    "expiry",
                                ),
                            ),
                            _negated=True,
                        ),
                        ("quantity__lt", 0),
                        _connector="OR",
                    ),
                ),
                name="a_move_carries_the_sign_of_its_type",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockmove",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("type__in", ("adjustment", "shrinkage", "expiry", "count")),
                        models.Q(("reason", ""), _negated=True),
                    ),
                    models.Q(
                        models.Q(
                            (
                                "type__in",
                                ("adjustment", "shrinkage", "expiry", "count"),
                            ),
                            _negated=True,
                        ),
                        ("reason", ""),
                    ),
                    _connector="OR",
                ),
                name="a_reason_belongs_to_a_reconciling_move",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockmove",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("reason", ""),
                    (
                        "reason__in",
                        (
                            "opening_stock",
                            "standalone_receipt",
                            "correction",
                            "damage",
                            "theft",
                            "loss",
                            "expired",
                            "count_adjustment",
                            "negative_resolution",
                        ),
                    ),
                    _connector="OR",
                ),
                name="move_reason_is_declared",
            ),
        ),
        migrations.AddIndex(
            model_name="stockonhand",
            index=models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="stock_on_hand_delta_cursor",
            ),
        ),
        migrations.AddIndex(
            model_name="stockonhand",
            index=models.Index(
                fields=["tenant", "updated_at", "id"],
                name="stock_on_hand_tenant_cursor",
            ),
        ),
        migrations.AddIndex(
            model_name="stockonhand",
            index=models.Index(
                fields=["tenant", "item", "location"], name="stock_on_hand_availability"
            ),
        ),
        migrations.AddConstraint(
            model_name="stockonhand",
            constraint=models.UniqueConstraint(
                fields=("tenant", "location", "item", "lot"),
                name="one_projection_row_per_key",
                nulls_distinct=False,
            ),
        ),
        migrations.AddIndex(
            model_name="stockpolicy",
            index=models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="stock_policies_delta_cursor",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockpolicy",
            constraint=models.UniqueConstraint(
                fields=("tenant", "item", "location"),
                name="one_policy_per_item_and_scope",
                nulls_distinct=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="stockpolicy",
            constraint=models.CheckConstraint(
                condition=models.Q(("source__in", ["manual", "model"])),
                name="policy_source_is_declared",
            ),
        ),
        migrations.AddIndex(
            model_name="transfer",
            index=models.Index(
                fields=["tenant", "status", "-number"], name="transfers_work_list"
            ),
        ),
        migrations.AddConstraint(
            model_name="transfer",
            constraint=models.UniqueConstraint(
                fields=("tenant", "number"), name="one_transfer_number_per_tenant"
            ),
        ),
        migrations.AddConstraint(
            model_name="transfer",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("status__in", ["draft", "dispatched", "received", "partial"])
                ),
                name="transfer_status_is_declared",
            ),
        ),
        migrations.AddConstraint(
            model_name="transfer",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("origin_location", models.F("destination_location")), _negated=True
                ),
                name="a_transfer_has_two_ends",
            ),
        ),
        migrations.AddConstraint(
            model_name="transferline",
            constraint=models.UniqueConstraint(
                fields=("tenant", "transfer", "item", "lot"),
                name="one_transfer_line_per_item_and_lot",
                nulls_distinct=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="transferline",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("resolution", ""),
                    ("resolution__in", ["received_late", "lost_in_transit"]),
                    _connector="OR",
                ),
                name="transfer_resolution_is_declared",
            ),
        ),
    ]
