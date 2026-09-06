"""S8's seven enum types and its four tables.

**Seven `CREATE TYPE` and no `ALTER TYPE`** (ownership.md, enum register).
`suggestion_type` is the ledger's, with its three values declared at creation;
the other six are coined by this stage on tables this stage creates, which is
what rule 1 covers. `cross_sell_basis` and `cross_sell_confidence` are
deliberately **scoped to this stage's own table** rather than claimed as a
shared `model_basis`/`model_confidence` pair: S6, S7 and S8 run in parallel
(§13), and a cross-stage enum three parallel stages each create is a clean build
that fails on the second migration.

**Nothing here touches `items`, `sale_lines` or any other stage's table.**
`sale_lines.from_suggestion` was created by S4 and is written by this stage's
service, not migrated by it; the item-grain sale index S6 created is what the
miner's co-occurrence self-join reads.

The RLS policies live in 0020, so the security layer stays one file somebody can
read in SQL -- the split S0 took between 0001 and 0002 and every stage since has
taken after it.
"""

import core.models
import django.contrib.postgres.fields
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


ENUMS = r"""
-- The ledger's, declared complete. Every value is **derived** by the pipeline
-- and none of them is ever chosen by a model (S8, *Rank, and label*).
CREATE TYPE suggestion_type AS ENUM (
    'first_choice',
    'conditional',
    'bought_together'
);

-- Coined. §3 gives `assistant_queries.mode` these two values and no type name.
-- `local` is what the till does offline, past the spend cap, with the switch
-- off, with the gateway down and when the output check rejected: five causes,
-- one behaviour.
CREATE TYPE assistant_mode AS ENUM ('model', 'local');

-- Coined name, values from §3.
CREATE TYPE item_warning_type AS ENUM (
    'interaction',
    'contraindication',
    'do_not_suggest_if'
);

-- Coined. `blocking` is what the filter reads. There is no third value and no
-- numeric scale, because a severity a build agent has to interpret is a filter
-- nobody can predict.
CREATE TYPE warning_severity AS ENUM ('blocking', 'advisory');

-- Coined. The ledger says warnings are loaded with the catalog and edited by
-- `owner`/`admin`; this is which of the two a row came from.
CREATE TYPE item_warning_source AS ENUM ('catalog', 'manual');

-- Coined. Which sale population a mining run consumed (§1). Without it a rule
-- mined from three weeks of Botica's own trading is indistinguishable from one
-- mined from eighteen months of imported history.
CREATE TYPE cross_sell_basis AS ENUM ('counter', 'imported', 'mixed');

-- Coined, and **not** `cross_sell_rules.confidence`, which is P(B|A) and an
-- association statistic. This is how much the miner knew, banded -- the
-- `Confianza del modelo` §1 asks every model surface to show.
CREATE TYPE cross_sell_confidence AS ENUM ('low', 'medium', 'high');
"""

ENUMS_REVERSE = r"""
DROP TYPE cross_sell_confidence;
DROP TYPE cross_sell_basis;
DROP TYPE item_warning_source;
DROP TYPE warning_severity;
DROP TYPE item_warning_type;
DROP TYPE assistant_mode;
DROP TYPE suggestion_type;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_pricing_rls"),
    ]

    operations = [
        # First, and before any table that names them.
        migrations.RunSQL(sql=ENUMS, reverse_sql=ENUMS_REVERSE),
        migrations.CreateModel(
            name="AssistantQuery",
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
                ("client_uuid", models.UUIDField()),
                (
                    "occurred_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "recorded_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("transcript", models.TextField(blank=True)),
                ("user_name", models.CharField(blank=True, max_length=200)),
                ("symptoms", models.JSONField(blank=True, default=list)),
                ("recommendation", models.TextField(blank=True)),
                ("recommendation_secondary", models.TextField(blank=True)),
                (
                    "mode",
                    core.models.EnumField(
                        choices=[("model", "Con modelo"), ("local", "Modo local")],
                        db_enum="assistant_mode",
                        max_length=8,
                    ),
                ),
                ("model", models.CharField(blank=True, max_length=120)),
                (
                    "cost_usd",
                    models.DecimalField(decimal_places=6, default=0, max_digits=10),
                ),
                ("latency_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("excluded", models.JSONField(blank=True, default=list)),
                ("output_check_passed", models.BooleanField(default=True)),
                (
                    "output_check_flags",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(), blank=True, default=list
                    ),
                ),
                ("candidate_count", models.PositiveIntegerField(default=0)),
                ("superseded_at", models.DateTimeField(blank=True, null=True)),
                ("bundle_version", models.CharField(blank=True, max_length=64)),
                ("ruleset_computed_at", models.DateTimeField(blank=True, null=True)),
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
                    "sale",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.sale",
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
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "assistant_queries",
                "ordering": ["-recorded_at"],
            },
        ),
        migrations.CreateModel(
            name="CrossSellRule",
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
                ("support", models.PositiveIntegerField()),
                ("confidence", models.DecimalField(decimal_places=4, max_digits=6)),
                ("lift", models.DecimalField(decimal_places=4, max_digits=10)),
                ("window", models.CharField(max_length=8)),
                (
                    "computed_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("rank", models.PositiveSmallIntegerField()),
                ("algorithm_version", models.CharField(max_length=64)),
                (
                    "basis",
                    core.models.EnumField(
                        choices=[
                            ("counter", "Venta propia"),
                            ("imported", "Historial importado"),
                            ("mixed", "Ambas"),
                        ],
                        db_enum="cross_sell_basis",
                        max_length=16,
                    ),
                ),
                ("ticket_count", models.PositiveIntegerField()),
                (
                    "confidence_band",
                    core.models.EnumField(
                        choices=[
                            ("low", "Baja"),
                            ("medium", "Media"),
                            ("high", "Alta"),
                        ],
                        db_enum="cross_sell_confidence",
                        max_length=8,
                    ),
                ),
                (
                    "item_a",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="core.item",
                    ),
                ),
                (
                    "item_b",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="core.item",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
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
                "db_table": "cross_sell_rules",
                "ordering": ["-lift"],
            },
        ),
        migrations.CreateModel(
            name="ItemWarning",
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
                        choices=[
                            ("interaction", "Interacción"),
                            ("contraindication", "Contraindicación"),
                            ("do_not_suggest_if", "No ofrecer si"),
                        ],
                        db_enum="item_warning_type",
                        max_length=24,
                    ),
                ),
                ("text", models.TextField()),
                (
                    "severity",
                    core.models.EnumField(
                        choices=[
                            ("blocking", "Bloqueante"),
                            ("advisory", "Informativa"),
                        ],
                        db_enum="warning_severity",
                        max_length=16,
                    ),
                ),
                (
                    "source",
                    core.models.EnumField(
                        choices=[
                            ("catalog", "Cargada con el catálogo"),
                            ("manual", "Escrita en Ajustes"),
                        ],
                        db_enum="item_warning_source",
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("triggers", models.JSONField(blank=True, default=list)),
                ("active", models.BooleanField(default=True)),
                (
                    "created_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="warnings",
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
                "db_table": "item_warnings",
                "ordering": ["item__name", "type"],
            },
        ),
        migrations.CreateModel(
            name="AssistantSuggestion",
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
                    "type",
                    core.models.EnumField(
                        choices=[
                            ("first_choice", "Primera opción"),
                            ("conditional", "Con condición"),
                            ("bought_together", "Se lleva junto"),
                        ],
                        db_enum="suggestion_type",
                        max_length=20,
                    ),
                ),
                ("reason", models.TextField(blank=True)),
                ("reason_code", models.CharField(blank=True, max_length=32)),
                ("price", models.DecimalField(decimal_places=2, max_digits=12)),
                ("rank", models.PositiveSmallIntegerField(default=1)),
                ("available_quantity", models.IntegerField(default=0)),
                (
                    "rule_confidence",
                    core.models.EnumField(
                        blank=True,
                        choices=[
                            ("low", "Baja"),
                            ("medium", "Media"),
                            ("high", "Alta"),
                        ],
                        db_enum="cross_sell_confidence",
                        max_length=8,
                        null=True,
                    ),
                ),
                ("accepted", models.BooleanField(default=False)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
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
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.location",
                    ),
                ),
                (
                    "query",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="suggestions",
                        to="core.assistantquery",
                    ),
                ),
                (
                    "sale_line",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.saleline",
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
                    "warning",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.itemwarning",
                    ),
                ),
            ],
            options={
                "db_table": "assistant_suggestions",
                "ordering": ["rank"],
            },
        ),
        migrations.AddIndex(
            model_name="assistantquery",
            index=models.Index(
                fields=["tenant", "recorded_at"], name="assistant_queries_by_period"
            ),
        ),
        migrations.AddIndex(
            model_name="assistantquery",
            index=models.Index(
                condition=models.Q(("sale__isnull", False)),
                fields=["tenant", "location", "sale"],
                name="assistant_queries_by_sale",
            ),
        ),
        migrations.AddIndex(
            model_name="assistantquery",
            index=models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="assistant_queries_cursor",
            ),
        ),
        migrations.AddConstraint(
            model_name="assistantquery",
            constraint=models.UniqueConstraint(
                fields=("tenant", "client_uuid"),
                name="one_assistant_query_per_client_uuid",
            ),
        ),
        migrations.AddIndex(
            model_name="crosssellrule",
            index=models.Index(
                fields=["tenant", "location", "item_a", "-lift"],
                name="cross_sell_by_anchor",
            ),
        ),
        migrations.AddIndex(
            model_name="crosssellrule",
            index=models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="cross_sell_delta_cursor",
            ),
        ),
        migrations.AddConstraint(
            model_name="crosssellrule",
            constraint=models.UniqueConstraint(
                fields=("tenant", "location", "item_a", "item_b"),
                name="one_rule_per_ordered_pair_per_scope",
                nulls_distinct=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="crosssellrule",
            constraint=models.CheckConstraint(
                condition=models.Q(("item_a", models.F("item_b")), _negated=True),
                name="an_item_is_never_paired_with_itself",
            ),
        ),
        migrations.AddIndex(
            model_name="itemwarning",
            index=models.Index(
                condition=models.Q(("active", True)),
                fields=["tenant", "item"],
                name="item_warnings_active_by_item",
            ),
        ),
        migrations.AddIndex(
            model_name="itemwarning",
            index=models.Index(
                fields=["tenant", "updated_at", "id"], name="item_warnings_delta_cursor"
            ),
        ),
        migrations.AddConstraint(
            model_name="itemwarning",
            constraint=models.CheckConstraint(
                condition=models.Q(("triggers__isnull", False)),
                name="a_warning_carries_a_trigger_array",
            ),
        ),
        migrations.AddIndex(
            model_name="assistantsuggestion",
            index=models.Index(
                fields=["tenant", "query", "rank"], name="assistant_cards_of_query"
            ),
        ),
        migrations.AddIndex(
            model_name="assistantsuggestion",
            index=models.Index(
                fields=["tenant", "recorded_at", "type"],
                name="assistant_offers_by_period",
            ),
        ),
        migrations.AddIndex(
            model_name="assistantsuggestion",
            index=models.Index(
                fields=["tenant", "location", "updated_at", "id"],
                name="assistant_suggestions_cur",
            ),
        ),
        migrations.AddConstraint(
            model_name="assistantsuggestion",
            constraint=models.UniqueConstraint(
                fields=("tenant", "client_uuid"), name="one_suggestion_per_client_uuid"
            ),
        ),
        migrations.AddConstraint(
            model_name="assistantsuggestion",
            constraint=models.UniqueConstraint(
                condition=models.Q(("sale_line__isnull", False)),
                fields=("tenant", "sale_line"),
                name="one_suggestion_per_sale_line",
            ),
        ),
        migrations.AddConstraint(
            model_name="assistantsuggestion",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("accepted", True), ("accepted_at__isnull", False)),
                    models.Q(("accepted", False), ("accepted_at__isnull", True)),
                    _connector="OR",
                ),
                name="an_accepted_suggestion_is_dated",
            ),
        ),
        migrations.AddConstraint(
            model_name="assistantsuggestion",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("reason_code", ""),
                    (
                        "reason_code__in",
                        (
                            "symptom_primary",
                            "symptom_secondary",
                            "warning_conditional",
                            "bought_together_location",
                            "bought_together_network",
                            "ticket_companion",
                            "substitute_available",
                            "no_candidates",
                        ),
                    ),
                    _connector="OR",
                ),
                name="a_suggestion_reason_code_is_declared",
            ),
        ),
    ]
