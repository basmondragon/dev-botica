"""The internal load tool.

Two outcomes are wrong and both are quiet: a run that loads nine rows and exits
zero, and a run that fails all ten because one was broken. Everything here is
about which of the two a given input produces.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import (
    Category,
    Customer,
    ImportRun,
    Item,
    ItemBarcode,
    ItemPrice,
    Manufacturer,
    Supplier,
    SupplierItem,
)

ITEMS_HEADER = (
    "external_code,type,name,presentation,manufacturer,category,unit,"
    "units_per_pack,vat_class,invima_registration,invima_status\n"
)


def write(directory, name, text):
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def items_csv(rows):
    return ITEMS_HEADER + "".join(rows)


def row(code, name, *, vat="excluded", lab="Genfar", category="Medicamentos"):
    return (
        f"{code},product,{name},caja × 30,{lab},{category},caja,1,{vat},"
        f"INVIMA 2019M-000{code[-4:]},valid\n"
    )


@pytest.fixture
def export(tmp_path, tenant_a):
    Manufacturer.objects.create(tenant=tenant_a, name="Genfar")
    Category.objects.create(tenant=tenant_a, name="Medicamentos")
    return tmp_path


def load(tenant, directory, **options):
    call_command("load_catalog", tenant=str(tenant.id), dir=str(directory), **options)


@pytest.mark.django_db
def test_the_tool_refuses_to_start_without_an_explicit_tenant(export):
    """Acceptance 15 and 17 · under FORCE ROW LEVEL SECURITY an unpinned run
    writes nothing and reports success, which is the worse of the two."""
    with pytest.raises(CommandError):
        call_command("load_catalog", dir=str(export))


@pytest.mark.django_db
def test_a_run_is_a_dry_run_unless_apply_is_passed_and_records_itself(export, tenant_a):
    """Acceptance 15."""
    write(export, "items.csv", items_csv([row("A-1", "Uno"), row("A-2", "Dos")]))
    load(tenant_a, export)

    assert Item.objects.filter(tenant=tenant_a).count() == 0
    run = ImportRun.objects.get(tenant=tenant_a)
    assert run.dry_run is True
    assert run.kind == "catalog"
    assert (run.rows_read, run.rows_created, run.rows_failed) == (2, 2, 0)
    assert run.started_by_user_id is None

    load(tenant_a, export, apply=True)
    assert Item.objects.filter(tenant=tenant_a).count() == 2
    applied = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert applied.dry_run is False
    assert applied.rows_created == 2


@pytest.mark.django_db
def test_the_same_file_twice_creates_nothing_and_updates_everything(export, tenant_a):
    """Acceptance 15 · idempotency is per entity, on a natural key."""
    write(export, "items.csv", items_csv([row("A-1", "Uno")]))
    load(tenant_a, export, apply=True)
    load(tenant_a, export, apply=True)
    assert Item.objects.filter(tenant=tenant_a).count() == 1
    second = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert (second.rows_created, second.rows_updated) == (0, 1)


@pytest.mark.django_db
def test_a_row_with_no_vat_class_fails_and_the_run_exits_non_zero(export, tenant_a):
    """Acceptance 16 · the one field the loader will not infer."""
    write(
        export,
        "items.csv",
        items_csv(
            [row(f"A-{index}", f"Producto {index}") for index in range(9)]
            + [row("A-9", "Roto", vat="")]
        ),
    )
    with pytest.raises(SystemExit):
        load(tenant_a, export, apply=True)

    assert Item.objects.filter(tenant=tenant_a).count() == 9
    run = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert (run.rows_read, run.rows_created, run.rows_failed) == (10, 9, 1)
    assert run.status == "failed"
    entry = run.errors[0]
    assert entry["file"] == "items.csv"
    assert entry["line"] == 11
    assert "IVA" in entry["reason"]


@pytest.mark.django_db
def test_the_operators_vat_class_is_recorded_rather_than_assumed(export, tenant_a):
    """Acceptance 16 · a guess becomes a recorded operator decision."""
    write(export, "items.csv", items_csv([row("A-0", "Sin IVA", vat="")]))
    load(tenant_a, export, apply=True, vat_class_when_missing="excluded")
    assert Item.objects.filter(tenant=tenant_a).count() == 1
    run = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert run.rows_failed == 0
    assert "1 fila(s) tomaron la clase de IVA" in run.errors[0]["reason"]


@pytest.mark.django_db
def test_a_row_naming_an_absent_laboratorio_fails_rather_than_creating_one(
    export, tenant_a
):
    """A placeholder laboratorio called `GENFAR ` with a trailing space is
    precisely the debris a catalog cleanup exists to remove."""
    write(
        export,
        "items.csv",
        items_csv([row("A-0", "Bueno"), row("A-1", "Malo", lab="Laboratorio Aurora")]),
    )
    with pytest.raises(SystemExit):
        load(tenant_a, export, apply=True)
    assert Manufacturer.objects.filter(tenant=tenant_a).count() == 1
    assert Item.objects.filter(tenant=tenant_a).count() == 1
    run = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert "Laboratorio Aurora" in run.errors[0]["reason"]


@pytest.mark.django_db
def test_a_run_pinned_to_one_tenant_writes_nothing_into_another(
    export, tenant_a, tenant_b
):
    """Acceptance 17 · a reference belonging to another network is simply not
    there under the pin, so the row fails rather than reaching across."""
    Manufacturer.objects.create(tenant=tenant_b, name="Laboratorio Aurora")
    write(
        export, "items.csv", items_csv([row("A-0", "Ajeno", lab="Laboratorio Aurora")])
    )
    with pytest.raises(SystemExit):
        load(tenant_a, export, apply=True)
    assert Item.objects.filter(tenant=tenant_a).count() == 0
    assert Item.objects.filter(tenant=tenant_b).count() == 0


@pytest.mark.django_db
def test_the_whole_contract_loads_in_one_run(export, tenant_a, sede_a):
    """The eight files, in the reference order, each resolving the last."""
    write(export, "manufacturers.csv", "name,nit\nTecnoquímicas,999.100.002-2\n")
    write(
        export,
        "categories.csv",
        "name,parent\nMedicamentos,\nAnalgésicos,Medicamentos\n",
    )
    write(
        export,
        "suppliers.csv",
        "nit,name,contact,payment_terms,lead_time_days\n"
        "999.200.001-1,Coopidrogas,Pedidos,30 días,2\n",
    )
    write(
        export,
        "items.csv",
        items_csv(
            [row("A-0", "Dipirona 500 mg × 10", category="Medicamentos > Analgésicos")]
        ),
    )
    write(export, "item_barcodes.csv", "item,code,is_primary\nA-0,7701234567890,1\n")
    write(
        export,
        "supplier_items.csv",
        "supplier,item,supplier_code,cost,min_order_pack,is_preferred\n"
        "999.200.001-1,A-0,CP-1,8.500,1,1\n",
    )
    write(
        export,
        "item_prices.csv",
        f"item,location,price,effective_from,effective_to\n"
        f"A-0,{sede_a.code},9.900,2026-01-01,\n"
        f"A-0,,12.500,2026-01-01,\n",
    )
    write(
        export,
        "customers.csv",
        "document_type,document,name,phone,email,address,data_consent,notes\n"
        "CC,900000001,Hernando Villamil,,,,1,\n",
    )
    load(tenant_a, export, apply=True)

    item = Item.objects.get(tenant=tenant_a, external_code="A-0")
    assert item.category.name == "Analgésicos"
    assert item.category.parent.name == "Medicamentos"
    assert ItemBarcode.objects.get(item=item).code == "7701234567890"
    link = SupplierItem.objects.get(item=item)
    assert str(link.cost) == "8500.00"
    assert link.is_preferred is True
    assert ItemPrice.objects.filter(item=item).count() == 2
    # Every price the loader writes is `imported`, with no author and no
    # proposal: no person typed it and no model produced it (A11).
    assert set(ItemPrice.objects.values_list("source", flat=True)) == {"imported"}
    assert ItemPrice.objects.filter(set_by_user__isnull=False).count() == 0
    assert Customer.objects.get(tenant=tenant_a).data_consent is True
    assert Supplier.objects.filter(tenant=tenant_a).count() == 1


@pytest.mark.django_db
def test_a_misspelled_header_is_named_rather_than_silently_dropped(export, tenant_a):
    write(export, "manufacturers.csv", "nombre,nit\nGenfar,1\n")
    with pytest.raises(SystemExit):
        load(tenant_a, export, apply=True)
    run = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert "nombre" in run.errors[0]["reason"]


@pytest.mark.django_db
def test_the_loader_never_deletes_and_never_deactivates(export, tenant_a):
    """A product missing from a new export is a product the export forgot."""
    write(export, "items.csv", items_csv([row("A-0", "Uno"), row("A-1", "Dos")]))
    load(tenant_a, export, apply=True)
    write(export, "items.csv", items_csv([row("A-0", "Uno")]))
    load(tenant_a, export, apply=True)
    assert Item.objects.filter(tenant=tenant_a, active=True).count() == 2


@pytest.mark.django_db
def test_a_service_row_that_fills_a_meaningless_column_is_refused(export, tenant_a):
    write(
        export,
        "items.csv",
        ITEMS_HEADER
        + "S-1,service,Toma de presión,caja × 30,,,servicio,1,excluded,,\n",
    )
    with pytest.raises(SystemExit):
        load(tenant_a, export, apply=True)
    run = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert "presentation" in run.errors[0]["reason"]


@pytest.mark.django_db
def test_a_narrower_file_leaves_the_columns_it_does_not_carry_alone(export, tenant_a):
    """§11.2 · **a run may supply a subset.**

    An omitted column leaves the stored value alone; a blank cell in a column
    the file does carry still clears it. Without that distinction a four-column
    price update wipes the laboratorio and the registro off every item it names,
    and reactivates the ones an administrator deactivated.
    """
    write(export, "items.csv", items_csv([row("A-0", "Completo")]))
    load(tenant_a, export, apply=True)
    item = Item.objects.get(tenant=tenant_a, external_code="A-0")
    assert item.manufacturer is not None
    assert item.invima_registration
    Item.objects.filter(id=item.id).update(active=False, controlled=True)

    # The narrow file a real onboarding sends second: a name and a unit, and
    # nothing else the export happened to hold.
    write(
        export,
        "items.csv",
        "external_code,name,unit,vat_class\nA-0,Completo,caja,excluded\n",
    )
    load(tenant_a, export, apply=True)

    item.refresh_from_db()
    assert item.manufacturer is not None
    assert item.invima_registration
    assert item.invima_status == "valid"
    assert item.controlled is True
    # It never deletes and never deactivates -- and never *re*activates either.
    assert item.active is False


@pytest.mark.django_db
def test_the_items_file_takes_the_same_barcode_guards_as_the_barcode_file(
    export, tenant_a
):
    """A code already held by another item is refused rather than reassigned,
    and a new primary demotes the previous one instead of colliding with it."""
    write(
        export,
        "items.csv",
        ITEMS_HEADER.rstrip("\n") + ",barcode\n"
        "A-0,product,Uno,caja × 10,Genfar,Medicamentos,caja,1,excluded,,,7700000000001\n"
        "A-1,product,Dos,caja × 10,Genfar,Medicamentos,caja,1,excluded,,,7700000000002\n",
    )
    load(tenant_a, export, apply=True)
    assert ItemBarcode.objects.filter(tenant=tenant_a, is_primary=True).count() == 2

    # The same directory again: the primary must not collide with itself.
    load(tenant_a, export, apply=True)
    assert ItemBarcode.objects.filter(tenant=tenant_a, is_primary=True).count() == 2

    # And a file that hands one item's code to another fails that row.
    write(
        export,
        "items.csv",
        ITEMS_HEADER.rstrip("\n") + ",barcode\n"
        "A-1,product,Dos,caja × 10,Genfar,Medicamentos,caja,1,excluded,,,7700000000001\n",
    )
    with pytest.raises(SystemExit):
        load(tenant_a, export, apply=True)
    run = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert "«Uno»" in run.errors[0]["reason"]


@pytest.mark.django_db
def test_re_applying_a_customers_file_does_not_move_the_consent_stamp(export, tenant_a):
    """The moment consent was given is not the moment a file was re-applied."""
    write(
        export,
        "customers.csv",
        "document_type,document,name,data_consent\nCC,900000001,Hernando,1\n",
    )
    load(tenant_a, export, apply=True)
    stamped = Customer.objects.get(tenant=tenant_a).data_consent_at
    assert stamped is not None

    load(tenant_a, export, apply=True)
    second = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert (second.rows_created, second.rows_updated) == (0, 1)
    assert Customer.objects.get(tenant=tenant_a).data_consent_at == stamped


@pytest.mark.django_db
def test_an_unreadable_file_is_reported_and_the_run_still_records_itself(
    export, tenant_a
):
    """A run that rolled back over one unreadable file would lose the rows every
    other file loaded, and leave no `imports` row saying why."""
    write(export, "items.csv", items_csv([row("A-0", "Bueno")]))
    (export / "customers.csv").write_bytes(b"document_type,document\n\xff\xfe\x00bad\n")
    with pytest.raises(SystemExit):
        load(tenant_a, export, apply=True)

    assert Item.objects.filter(tenant=tenant_a).count() == 1
    run = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert run.rows_created == 1
    assert any(entry["file"] == "customers.csv" for entry in run.errors)


@pytest.mark.django_db
def test_an_english_figure_is_refused_rather_than_read_a_thousandfold_small(
    export, tenant_a, sede_a
):
    """`1,234.56` under the Colombian convention would be 1,23456 pesos: a
    price a thousand times too small, loaded silently, onto a shelf."""
    write(export, "items.csv", items_csv([row("A-0", "Uno")]))
    write(
        export,
        "item_prices.csv",
        "item,location,price,effective_from,effective_to,per_pack\n"
        'A-0,,"1,234.56",2026-01-01,,\n',
    )
    with pytest.raises(SystemExit):
        load(tenant_a, export, apply=True)
    run = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert any("a la inglesa" in entry["reason"] for entry in run.errors)
    assert ItemPrice.objects.filter(tenant=tenant_a).count() == 0


@pytest.mark.django_db
def test_a_price_per_pack_is_converted_by_units_per_pack(export, tenant_a):
    """§11.2 · the same conversion receiving and supplier cost use. A loader
    that guessed would put a box price on a tableta."""
    write(
        export,
        "items.csv",
        ITEMS_HEADER.rstrip("\n") + ",splittable\n"
        "A-0,product,Ibuprofeno 400 mg × 30,caja × 30,Genfar,Medicamentos,"
        "tableta,30,excluded,,,1\n",
    )
    write(
        export,
        "item_prices.csv",
        "item,location,price,effective_from,effective_to,per_pack\n"
        "A-0,,12.500,2026-01-01,,1\n",
    )
    load(tenant_a, export, apply=True)
    price = ItemPrice.objects.get(tenant=tenant_a)
    # 12.500 the box, 30 tabletas in it, so $416,67 the base unit.
    assert str(price.price) == "416.67"


@pytest.mark.django_db
def test_a_codeless_export_refuses_its_own_second_run(export, tenant_a):
    """§11.2 · where an export carries neither a code nor a barcode, a second
    run **refuses those rows rather than silently doubling the catalog** -- the
    failure is loud, which is the whole reason the fallback is named."""
    write(
        export,
        "items.csv",
        "type,name,presentation,manufacturer,category,unit,vat_class\n"
        "product,Sin código,caja × 10,Genfar,Medicamentos,caja,excluded\n",
    )
    load(tenant_a, export, apply=True)
    assert Item.objects.filter(tenant=tenant_a).count() == 1

    with pytest.raises(SystemExit):
        load(tenant_a, export, apply=True)
    assert Item.objects.filter(tenant=tenant_a).count() == 1
    run = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
    assert "código externo" in run.errors[0]["reason"]


@pytest.mark.django_db
def test_a_service_row_is_refused_for_every_column_it_cannot_have(export, tenant_a):
    """The eleven columns the service table leaves without meaning."""
    base = (
        "external_code,type,name,unit,vat_class,{column}\n"
        "S-1,service,Toma de presión,servicio,excluded,{value}\n"
    )
    for column, value in [
        ("presentation", "caja × 30"),
        ("manufacturer", "Genfar"),
        ("active_ingredient", "nada"),
        ("strength", "500 mg"),
        ("invima_registration", "INVIMA 2019M-0000001"),
        ("invima_expires_at", "2027-01-01"),
        ("requires_prescription", "1"),
        ("controlled", "1"),
        ("cold_chain", "1"),
        ("splittable", "1"),
        ("tracks_lots", "1"),
        ("tracks_expiry", "1"),
        ("units_per_pack", "30"),
        ("invima_status", "valid"),
    ]:
        write(export, "items.csv", base.format(column=column, value=value))
        with pytest.raises(SystemExit):
            load(tenant_a, export, apply=True)
        run = ImportRun.objects.filter(tenant=tenant_a).order_by("-started_at").first()
        assert run.rows_failed == 1, column
    assert Item.objects.filter(tenant=tenant_a, type="service").count() == 0
