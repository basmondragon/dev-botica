"""S1's `catalog` fixture inside S0's `seed_demo_tenant`.

The command is S0's; what these check is the fixture. A stage is not finished
until its screens render convincingly from the seed, and *convincing* is
verifiable rather than a feeling: the counts the grid's own footer and filter
chips report, the drawn products with the drawn laboratorio beside each, and the
figures the Mostrador ticket totals to.

**Every figure asserted here is the seed's own or the product's own** -- never a
production sizing figure, which would fail on every run against a seed that
holds what its fixtures built.
"""

from decimal import Decimal

import pytest
from django.core.management import call_command

from core.catalog import prices, vocabulary as vocab
from core.catalog.demo import (
    CATALOG_SIZE,
    MINIMAL_MANUFACTURER,
    RESERVED_ERASED,
    RESERVED_FOR_SALES,
    RESERVED_NEVER_REFERENCED,
)
from core.demo import identity, registry
from core.models import (
    Customer,
    ImportRun,
    Item,
    ItemBarcode,
    ItemPrice,
    Manufacturer,
    SupplierItem,
)


def seed(profile):
    call_command("seed_demo_tenant", profile=profile)
    return registry.uid(profile, "tenants", identity.slug_for(profile))


def items(tenant_id):
    return Item.objects.filter(tenant_id=tenant_id)


@pytest.mark.django_db
def test_the_registry_runs_the_catalog_after_the_identity_it_depends_on():
    """Acceptance 21 · a `requires` declared the wrong way round shows up here
    and nowhere else."""
    order = [fixture.name for fixture in registry.ordered()]
    assert order.index("identity") < order.index("catalog")
    assert registry.REGISTRY["catalog"].requires == ("identity",)
    assert set(registry.REGISTRY["catalog"].guard_tables) == {
        "items",
        "customers",
        "imports",
    }


@pytest.mark.django_db
def test_the_default_profile_builds_the_drawn_catalog():
    """Acceptance 18 and 19 · the drawn screen reached by one command and
    nothing typed by hand."""
    tenant_id = seed("default")
    assert items(tenant_id).count() == CATALOG_SIZE
    assert items(tenant_id).filter(type="service").count() == len(vocab.SERVICES)
    assert Manufacturer.objects.filter(tenant_id=tenant_id).count() == 7
    assert (
        Manufacturer.objects.filter(tenant_id=tenant_id, name="Coopidrogas").exists()
        is False
    )

    # `1-15 de 4.284`, and the pagination ending at 172 at 25 rows a page.
    assert -(-CATALOG_SIZE // 25) == 172

    for name, laboratorio in [(row[0], row[2]) for row in vocab.DRAWN[:15]]:
        item = items(tenant_id).get(name=name)
        assert item.manufacturer.name == laboratorio, name
    for name in vocab.COMPRAS_ROWS:
        assert items(tenant_id).filter(name=name).exists(), name


@pytest.mark.django_db
def test_the_mostrador_ticket_totals_fifteen_thousand_six_hundred():
    """Acceptance 19 · the drawn figures are exact where a screen shows one."""
    tenant_id = seed("default")
    resolved = {}
    for name, expected in vocab.MOSTRADOR_PRICES.items():
        item = items(tenant_id).get(name=name)
        row = prices.in_force(item.id)
        assert row is not None and row.price == Decimal(expected), name
        resolved[name] = row.price
    total = (
        2 * resolved["Sales de rehidratación oral"]
        + resolved["Acetaminofén 500 mg × 10"]
        + resolved["Electrolitos bebida 500 ml"]
    )
    assert total == Decimal("15600")


@pytest.mark.django_db
def test_the_grid_has_something_behind_every_filter_chip():
    """Check 11 · wrong looks like a laboratorio chip with one entry, a footer
    annotation of zero, or a services filter that returns nothing."""
    tenant_id = seed("default")
    rows = items(tenant_id)
    statuses = set(rows.values_list("invima_status", flat=True))
    assert statuses == {"valid", "in_process", "expired", "not_applicable"}
    assert rows.filter(invima_status="expired").count() > 0
    assert rows.filter(type="service").count() == 5
    assert (
        Manufacturer.objects.filter(tenant_id=tenant_id, items__isnull=False)
        .distinct()
        .count()
        == 7
    )
    # A laboratorio and a price on every product row, and no `$0`.
    assert rows.filter(type="product", manufacturer__isnull=True).count() == 0
    assert ItemPrice.objects.filter(tenant_id=tenant_id, price__lte=0).count() == 0
    assert (
        rows.exclude(
            id__in=ItemPrice.objects.filter(tenant_id=tenant_id).values("item_id")
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_every_seeded_opening_price_is_imported_and_carries_no_author():
    """The exception is deliberate: the repricings are `manual` with the seeded
    administrator's id, because a repricing is a person's act."""
    tenant_id = seed("default")
    rows = ItemPrice.objects.filter(tenant_id=tenant_id)
    assert rows.filter(proposal_id__isnull=False).count() == 0
    imported = rows.filter(source="imported")
    assert imported.count() == CATALOG_SIZE
    assert imported.filter(set_by_user__isnull=False).count() == 0
    manual = rows.filter(source="manual")
    assert manual.count() > 0
    assert manual.filter(set_by_user__isnull=True).count() == 0
    assert manual.filter(set_by_name="").count() == 0
    assert manual.first().set_by_user.role in ("admin", "owner")
    # The name is stamped, not joined: a demo whose price history lost its
    # author the day somebody was removed from the roster would be showing a
    # column that never fills.
    assert manual.first().set_by_name == manual.first().set_by_user.name


@pytest.mark.django_db
def test_a_second_run_changes_nothing_and_every_id_is_the_one_it_had():
    """Acceptance 20 · an id the database assigned rather than one derived from
    a natural key silently breaks every saved link, screenshot and bug report
    after a reset."""
    tenant_id = seed("default")
    before = {
        "items": set(items(tenant_id).values_list("id", flat=True)),
        "prices": set(
            ItemPrice.objects.filter(tenant_id=tenant_id).values_list("id", flat=True)
        ),
        "customers": set(
            Customer.objects.filter(tenant_id=tenant_id).values_list("id", flat=True)
        ),
    }
    stamps = set(items(tenant_id).values_list("id", "updated_at"))

    seed("default")
    assert set(items(tenant_id).values_list("id", flat=True)) == before["items"]
    assert (
        set(ItemPrice.objects.filter(tenant_id=tenant_id).values_list("id", flat=True))
        == before["prices"]
    )
    assert (
        set(Customer.objects.filter(tenant_id=tenant_id).values_list("id", flat=True))
        == before["customers"]
    )
    assert set(items(tenant_id).values_list("id", "updated_at")) == stamps
    assert ImportRun.objects.filter(tenant_id=tenant_id).count() == 1


@pytest.mark.django_db
def test_the_guard_refuses_a_tenant_holding_one_row_it_did_not_write():
    """Acceptance 20 · a seed run against a live tenant would merge four
    thousand fictional products into a real catalog with no undo."""
    from django.core.management.base import CommandError

    tenant_id = seed("default")
    Item.objects.create(
        tenant_id=tenant_id,
        type="product",
        name="A mano",
        unit="caja",
        vat_class="excluded",
        invima_status="not_applicable",
    )
    with pytest.raises(CommandError) as refusal:
        seed("default")
    assert "items" in str(refusal.value)


@pytest.mark.django_db
def test_the_cold_profile_has_one_open_price_per_item_and_nothing_closed():
    """A price history with no volume behind it is a signal S7 would be right
    to ignore."""
    tenant_id = seed("cold")
    assert items(tenant_id).count() == CATALOG_SIZE
    rows = ItemPrice.objects.filter(tenant_id=tenant_id)
    assert rows.count() == CATALOG_SIZE
    assert rows.filter(effective_to__isnull=False).count() == 0
    assert rows.filter(location__isnull=False).count() == 0
    assert rows.filter(source="manual").count() == 0


@pytest.mark.django_db
def test_the_young_profile_moves_its_prices_inside_its_own_window():
    """A price that moves outside the history S4 wrote co-moves with nothing,
    which is the one thing those rows exist to do."""
    from datetime import timedelta

    from django.utils import timezone

    tenant_id = seed("young")
    assert items(tenant_id).count() == CATALOG_SIZE
    moved = ItemPrice.objects.filter(tenant_id=tenant_id, source="manual")
    assert moved.count() > 0
    edge = timezone.localdate() - timedelta(days=13)
    # Every repricing sits inside the twelve-day window; the sede overrides sit
    # outside it by design and are excluded by scope.
    assert moved.filter(location__isnull=True, effective_from__lt=edge).count() == 0


@pytest.mark.django_db
def test_the_scale_profile_keeps_the_catalog_and_spreads_the_overrides():
    """Twenty sedes do not make a network sell more references."""
    from core.models import Location

    tenant_id = seed("scale")
    assert items(tenant_id).count() == CATALOG_SIZE
    assert Location.objects.filter(tenant_id=tenant_id).count() == 20
    scoped = ItemPrice.objects.filter(tenant_id=tenant_id, location__isnull=False)
    assert scoped.values("location_id").distinct().count() == 20


@pytest.mark.django_db
def test_the_minimal_profile_is_a_token_catalog_nobody_could_confuse():
    """It exists to be the tenant another tenant is isolated *from*."""
    tenant_id = seed("minimal")
    assert items(tenant_id).count() == 12
    assert Customer.objects.filter(tenant_id=tenant_id).count() == 2
    labs = Manufacturer.objects.filter(tenant_id=tenant_id)
    assert [lab.name for lab in labs] == [MINIMAL_MANUFACTURER]
    # The name appears in no other profile, which is what makes the load tool's
    # cross-tenant check meaningful.
    assert MINIMAL_MANUFACTURER not in [name for name, _nit in vocab.MANUFACTURERS]
    assert ItemPrice.objects.filter(tenant_id=tenant_id).count() == 12
    assert ItemBarcode.objects.filter(tenant_id=tenant_id).count() == 12


@pytest.mark.django_db
def test_the_three_reserved_customers_are_there_and_one_is_already_erased():
    """Check 9 · a check that reaches its own state by hand-editing rows is a
    check nobody runs twice."""
    tenant_id = seed("default")
    rows = Customer.objects.filter(tenant_id=tenant_id)
    never = rows.get(
        document_type=RESERVED_NEVER_REFERENCED[0],
        document=RESERVED_NEVER_REFERENCED[1],
    )
    for_sales = rows.get(
        document_type=RESERVED_FOR_SALES[0], document=RESERVED_FOR_SALES[1]
    )
    erased = rows.get(id=registry.uid("default", "customers", RESERVED_ERASED))
    assert never.name and for_sales.name
    assert erased.erased is True
    assert erased.name == "" and erased.document == "" and erased.document_type == ""
    assert erased.data_consent is False and erased.data_consent_at is None


@pytest.mark.django_db
def test_the_run_writes_one_imports_row_that_marks_the_tenant_synthetic():
    """Invariant 1 · that row is both the record of the run and the marker on
    the tenant, and it is this fixture's to write because `imports` is S1's
    table and does not exist at S0."""
    tenant_id = seed("default")
    row = ImportRun.objects.get(tenant_id=tenant_id)
    assert row.kind == "demo_seed"
    assert "default" in row.source
    assert row.started_by_user_id is None
    assert "items" in row.errors[0]["reason"]


@pytest.mark.django_db
def test_the_seeded_data_is_self_evidently_synthetic():
    """What is on screen reads as real; what is in the database does not."""
    tenant_id = seed("default")
    for _name, nit in vocab.MANUFACTURERS:
        assert nit.startswith("999.")
    for nit, *_rest in vocab.SUPPLIERS:
        assert nit.startswith("999.")
    for customer in Customer.objects.filter(tenant_id=tenant_id).exclude(email=""):
        assert customer.email.endswith("@example.com")
    for customer in Customer.objects.filter(tenant_id=tenant_id).exclude(phone=""):
        assert customer.phone == "+57 601 000 0000"


@pytest.mark.django_db
def test_every_item_has_a_preferred_supplier_whose_cost_is_below_its_price():
    """So no margin figure on any screen is negative."""
    tenant_id = seed("default")
    products = items(tenant_id).filter(type="product")
    preferred = SupplierItem.objects.filter(tenant_id=tenant_id, is_preferred=True)
    assert preferred.count() == products.count()
    for link in preferred.select_related("item")[:200]:
        row = prices.in_force(link.item_id)
        assert link.cost < row.price * link.item.units_per_pack


@pytest.mark.django_db
def test_a_fixture_that_raises_leaves_the_tenant_exactly_as_it_was():
    """Acceptance 21 · a half-seeded tenant is worse than an unseeded one.

    The single transaction is S0's; what this checks is that S1's fixture is
    inside it, so a failure at row four thousand takes the first three thousand
    nine hundred with it.
    """
    from django.core.management.base import CommandError

    from core.catalog import demo as catalog

    tenant_id = seed("default")
    before = items(tenant_id).count()

    def explode(context):
        # Halfway through: some rows are already written when it raises.
        catalog._write_manufacturers(context, catalog.PROFILES[context.profile])
        raise RuntimeError("deliberate")

    original = registry.REGISTRY["catalog"]
    registry.REGISTRY["catalog"] = registry.Fixture(
        name=original.name,
        guard_tables=original.guard_tables,
        requires=original.requires,
        build=explode,
        owned_ids=original.owned_ids,
    )
    try:
        with pytest.raises((RuntimeError, CommandError)):
            call_command("seed_demo_tenant", profile="young")
    finally:
        registry.REGISTRY["catalog"] = original

    young = registry.uid("young", "tenants", identity.slug_for("young"))
    assert items(young).count() == 0
    assert Manufacturer.objects.filter(tenant_id=young).count() == 0
    # And the tenant that was already seeded is untouched.
    assert items(tenant_id).count() == before


@pytest.mark.django_db
def test_no_drawn_row_comes_out_fraccionable():
    """The Mostrador ticket charges `1 × $2.600` for a box of ten, so the drawn
    products are boxes the customer walks out with. One that came out
    fraccionable would divide its own drawn price by the pack and put a figure
    on screen the client was never shown."""
    tenant_id = seed("default")
    drawn = {row[0] for row in vocab.DRAWN}
    fraccionable = items(tenant_id).filter(name__in=drawn, splittable=True)
    assert list(fraccionable) == []
    for row in items(tenant_id).filter(name__in=drawn):
        assert row.units_per_pack == 1, row.name

    # And the catalog still shows the base-unit rule somewhere: a minority of
    # the generated half is fraccionable, or the rule is invisible on screen.
    assert items(tenant_id).filter(splittable=True).count() > 100


@pytest.mark.django_db
def test_a_document_number_names_the_same_person_in_every_profile():
    """`demo-la-45` and `demo-la-45-young` are the same network with a shorter
    history, so a customer's identity cannot depend on which one was built."""
    default_rows = {
        row.document: row.name
        for row in Customer.objects.filter(tenant_id=seed("default")).exclude(
            document=""
        )
    }
    young_rows = {
        row.document: row.name
        for row in Customer.objects.filter(tenant_id=seed("young")).exclude(document="")
    }
    assert default_rows == young_rows
