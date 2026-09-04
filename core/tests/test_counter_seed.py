"""The demo seed's counter fixture — this stage's completion test.

§1 makes a client's sales history an accelerant and never a precondition, so
where the export does not exist these two tables are the only history the
product has: S6's demand forecast, S7's elasticity, S8's `cross_sell_rules` and
every tile on S9's Panel read them. **A shape checked on the screen and not in a
query is a shape three later stages inherit and nobody re-derives**, so the
readings acceptance 32 names are asserted here rather than eyeballed.

Each check seeds a whole tenant, which is slow on purpose: it is the same
command a person runs before a demo, and a fixture that only ever ran inside a
narrower harness would pass here and fail there.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db.models import Count, Max, Min, Sum
from django.utils import timezone

from core.counter import demo as counter_demo
from core.demo import identity, registry as demo_registry
from core.inventory import ledger
from core.models import (
    Item,
    Location,
    LocationType,
    Payment,
    Sale,
    SaleLine,
    SaleReturn,
    SaleReturnLine,
    SaleStatus,
    Shift,
    ShiftStatus,
    StockMove,
    StockMoveType,
    StockOnHand,
)
from core.tenancy import pin_tenant

pytestmark = pytest.mark.django_db


def seed(profile):
    call_command("seed_demo_tenant", profile=profile)
    return demo_registry.uid(profile, "tenants", identity.slug_for(profile))


def test_the_default_seed_fills_the_counter_the_way_a_network_would():
    """Acceptance 31 · a till on any sede finds its open turno, the office list
    has sales, turnos with real variances and devoluciones at every sede, and
    the average-ticket note computes rather than reading `—`."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        sedes = list(
            Location.objects.filter(tenant_id=tenant_id).values_list("id", flat=True)
        )
        assert Sale.objects.count() > 1000
        assert SaleLine.objects.count() > Sale.objects.count()
        assert (
            Payment.objects.count()
            >= Sale.objects.filter(status=SaleStatus.CLOSED).count()
        )

        # **One open turno per sede**, so a till opened anywhere finds one and
        # sells -- and never two on one device, which the partial unique index
        # refuses.
        open_shifts = Shift.objects.filter(status=ShiftStatus.OPEN)
        assert set(open_shifts.values_list("location_id", flat=True)) == set(sedes)
        assert open_shifts.count() == len(sedes)

        # **Never a column of zeros**: a seed where every drawer reconciles
        # perfectly makes `variance` look like decoration and hides the
        # arithmetic error that would produce the same column.
        closed = Shift.objects.filter(status=ShiftStatus.CLOSED)
        assert closed.exclude(variance=Decimal("0")).exists()
        assert closed.filter(variance=Decimal("0")).exists()
        assert closed.filter(variance__lt=0).exists()
        assert closed.filter(variance__gt=0).exists()
        assert not closed.filter(declared_total__isnull=True).exists()

        # Devoluciones at every sede, so the list is not one sede's screen.
        returned = set(
            SaleReturn.objects.values_list("location_id", flat=True).distinct()
        )
        assert returned == set(sedes)
        assert SaleReturnLine.objects.count() == SaleReturn.objects.count()

        # A ticket in progress, so `Mostrador 3` has a number to report -- and
        # an open sale carries no lines, because a till never pushes a line
        # until the batch that closes its ticket.
        open_sales = Sale.objects.filter(status=SaleStatus.OPEN)
        assert open_sales.count() == counter_demo.OPEN_TICKETS
        assert not SaleLine.objects.filter(sale__in=open_sales).exists()

        # The average-ticket note computes: the head sede has far more than the
        # twenty tickets §B.9.2's tier 3 withholds below.
        window = timezone.now() - timedelta(days=7)
        recent = Sale.objects.filter(
            location_id=sedes[0], status=SaleStatus.CLOSED, occurred_at__gte=window
        )
        assert recent.count() >= 20


def test_the_seeded_shape_is_the_one_two_later_stages_are_built_on():
    """Acceptance 32 · **queried rather than looked at**, because S6, S7, S8 and
    S9 all stand on it.

    180 days at the five established sedes and about three weeks at Usme; the
    per-sede totals ordered as the handoff's `Venta por sede` list draws them
    with Usme last; and every row `source = counter`.
    """
    tenant_id = seed("default")
    today = timezone.localdate()
    with pin_tenant(tenant_id):
        rows = list(
            Sale.objects.values("location__code")
            .annotate(first=Min("occurred_at"), total=Sum("total"), tickets=Count("id"))
            .order_by("-total")
        )
    by_code = {row["location__code"]: row for row in rows}

    # The handoff's own ranking, and it is the ordering S9's per-sede list draws.
    assert [row["location__code"] for row in rows] == [
        "CHA",
        "KEN",
        "SUB",
        "RES",
        "BOS",
        "USM",
    ]

    for code in ("CHA", "KEN", "SUB", "RES", "BOS"):
        span = (today - by_code[code]["first"].date()).days
        assert 175 <= span <= counter_demo.WINDOW_DAYS, code
    # **The one sede that demonstrates the cold-start decision.** With every
    # sede on 180 days every sede is `learned`, and the parametric path -- the
    # thing that makes the product demonstrable on a client with no history at
    # all -- is never seen in a demo.
    usme = (today - by_code["USM"]["first"].date()).days
    assert 14 <= usme <= counter_demo.COLD_SEDE_DAYS + 2

    with pin_tenant(tenant_id):
        assert Sale.objects.filter(source="imported").count() == 0
        # A counter sale outside a turno cannot be reconciled.
        assert Sale.objects.filter(shift__isnull=True).count() == 0


def test_the_seeded_sales_moved_stock_through_the_ledger_service():
    """Acceptance 31 · `stock_on_hand` for every seeded lot equals the sum of
    its `stock_moves`, **because the fixture appended them through S3's
    service**.

    A fixture that inserted rows and left the projection alone would produce an
    Existencias screen contradicting the Panel, and the first person to notice
    would be a prospect standing in front of both.
    """
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        assert StockMove.objects.filter(type=StockMoveType.SALE).exists()
        assert StockMove.objects.filter(type=StockMoveType.CUSTOMER_RETURN).exists()
        # Every seeded movement names the till it happened on and the person who
        # made it: a device column empty on twelve thousand rows is a trace
        # nobody would accept.
        assert not StockMove.objects.filter(
            type=StockMoveType.SALE, device__isnull=True
        ).exists()
        assert not StockMove.objects.filter(
            type=StockMoveType.SALE, user_name=""
        ).exists()
        for location in Location.objects.filter(tenant_id=tenant_id):
            assert ledger.verify(tenant_id, location.id) == {}

        # **`from_suggestion` is created empty and never written here** (ledger,
        # disputed columns). S8's fixture sets it, on exactly the lines its own
        # seeded suggestions became.
        assert not SaleLine.objects.filter(from_suggestion=True).exists()

        # No invoicing target is configured, nothing is handed anywhere, and
        # that is the default state rather than an error (§8).
        #
        # **Written before S5 existed as "the table is not there yet", and now
        # checked as what it always meant.** The table exists from S5 onward and
        # the seeded tenant holds no row in it, which is the stronger claim: an
        # absent table proves nothing about a product that ships one.
        from core.models import FiscalDocument

        assert not FiscalDocument.objects.exists()


def test_the_young_profile_is_a_young_network_and_not_a_young_sede():
    """§1 · twelve days of history **at all six sedes**. Usme is not cold inside
    it, and twelve is the figure S9's withheld comparators and S7's estimator
    floor are both checked against."""
    tenant_id = seed("young")
    with pin_tenant(tenant_id):
        rows = list(
            Sale.objects.values("location__code").annotate(
                first=Min("occurred_at"), last=Max("occurred_at")
            )
        )
        assert len(rows) == 6
        for row in rows:
            span = (row["last"] - row["first"]).days
            assert span <= counter_demo.YOUNG_DAYS, row["location__code"]
        overall = Sale.objects.aggregate(
            first=Min("occurred_at"), last=Max("occurred_at")
        )
        assert (overall["last"] - overall["first"]).days <= counter_demo.YOUNG_DAYS
        # **Nothing else shrinks**: a network that opened three weeks ago stocks
        # a full shop.
        assert Item.objects.count() > 4000
        assert StockOnHand.objects.exists()


def test_the_cold_profile_has_sold_nothing_and_still_has_a_shop():
    """§1 · zero rows in all six of this stage's tables and not one `sale`
    move — **while `items` and `stock_on_hand` both come back non-empty under
    the same pin**.

    That second half is what separates a seeded `cold` from a run that failed
    silently; without it a broken seed reads as a passing check.
    """
    tenant_id = seed("cold")
    with pin_tenant(tenant_id):
        for model in (Sale, SaleLine, Payment, Shift, SaleReturn, SaleReturnLine):
            assert model.objects.count() == 0, model.__name__
        assert StockMove.objects.filter(type=StockMoveType.SALE).count() == 0
        assert Item.objects.count() > 0
        assert StockOnHand.objects.count() > 0


def test_the_scale_profile_rings_nothing_at_the_bodega():
    """A bodega rings none, and it is the location a per-sede list has to render
    without dividing by zero."""
    tenant_id = seed("scale")
    with pin_tenant(tenant_id):
        warehouse = Location.objects.get(
            tenant_id=tenant_id, type=LocationType.WAREHOUSE
        )
        assert Sale.objects.filter(location=warehouse).count() == 0
        assert Shift.objects.filter(location=warehouse).count() == 0
        assert SaleReturn.objects.filter(location=warehouse).count() == 0
        selling = set(Sale.objects.values_list("location_id", flat=True).distinct())
        assert len(selling) == 19
        # A head and a tail rather than a nineteen-way tie, or the per-sede
        # ranking has nothing to rank.
        totals = sorted(
            row["total"]
            for row in Sale.objects.values("location_id").annotate(total=Sum("total"))
        )
        assert totals[-1] > totals[0] * 2


def test_the_minimal_profile_is_small_and_never_zero():
    """This is the tenant the isolation checks are isolated *from*, and a
    cross-tenant `sales` count of zero proves nothing when the other tenant has
    no sales either. Its single `owner` rings the till, because the profile has
    no cashier."""
    tenant_id = seed("minimal")
    with pin_tenant(tenant_id):
        assert Sale.objects.count() > 0
        assert Sale.objects.filter(shift__isnull=True).count() == 0
        assert Shift.objects.count() > 0
        assert Sale.objects.exclude(sold_by_name="").count() == Sale.objects.count()


def test_running_the_counter_fixture_twice_changes_nothing():
    """Every seeded id is derived from a natural key and every seeded move
    carries a derived `client_uuid`, so a re-run is a set of rows already
    present rather than a set of collisions."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        before = {
            "sales": Sale.objects.count(),
            "lines": SaleLine.objects.count(),
            "payments": Payment.objects.count(),
            "shifts": Shift.objects.count(),
            "returns": SaleReturn.objects.count(),
            "moves": StockMove.objects.count(),
        }
        projection = {
            (row.location_id, row.item_id, row.lot_id, row.quantity)
            for row in StockOnHand.objects.all()
        }
    seed("default")
    with pin_tenant(tenant_id):
        assert {
            "sales": Sale.objects.count(),
            "lines": SaleLine.objects.count(),
            "payments": Payment.objects.count(),
            "shifts": Shift.objects.count(),
            "returns": SaleReturn.objects.count(),
            "moves": StockMove.objects.count(),
        } == before
        assert {
            (row.location_id, row.item_id, row.lot_id, row.quantity)
            for row in StockOnHand.objects.all()
        } == projection


def test_the_ley_1581_erasure_branch_keeps_every_sale(client_as):
    """**The half S1 could not run.**

    S1 decides the rule and can prove only its first branch: at S1 nothing
    references `customers`, so every delete is a hard delete. The other branch
    becomes reachable the moment `sales` exists, which is this stage — and it is
    the only legally load-bearing mutation in the catalog, so it runs here or it
    is run by nobody.

    A 404, a `customer_id` gone null, a cascaded sale or a touched `updated_at`
    is **data loss** rather than a wrong screen, on the one button a droguería is
    legally obliged to be able to press.
    """
    from core.catalog import demo as catalog_demo
    from core.models import Customer, User, Role, AuditLog

    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        reserved = Customer.objects.get(
            tenant_id=tenant_id,
            document_type=catalog_demo.RESERVED_FOR_SALES[0],
            document=catalog_demo.RESERVED_FOR_SALES[1],
        )
        before = {
            row.id: (row.total, row.customer_id, row.updated_at)
            for row in Sale.objects.filter(customer=reserved)
        }
        owner = User.objects.get(tenant_id=tenant_id, role=Role.OWNER)
    # **The row it runs against is seeded by name rather than attached on the
    # spot**: a check that begins *first, hang a sale on a customer* is a check
    # nobody runs twice.
    assert len(before) > 0

    response = client_as(owner).delete(f"/api/customers/{reserved.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "erased"
    assert body["sale_count"] == len(before)

    # `GET` still returns the row: a 404 here is the failure and not the pass.
    read = client_as(owner).get(f"/api/customers/{reserved.id}").json()
    assert read["document_type"] == ""
    assert read["document"] == ""
    assert read["name"] == ""
    assert read["erased"] is True

    with pin_tenant(tenant_id):
        after = {
            row.id: (row.total, row.customer_id, row.updated_at)
            for row in Sale.objects.filter(customer_id=reserved.id)
        }
        assert after == before
        assert (
            AuditLog.objects.filter(
                entity_type="customers", entity_id=reserved.id
            ).count()
            == 1
        )


def test_sales_customer_id_is_neither_cascade_nor_set_null():
    """*What this stage would break* · a cascade deletes the sales when an
    administrator presses a legally required button, and a `SET NULL` keeps them
    and loses the acquirer S5 has to name on the canonical document. It is
    `PROTECT`, which is never reached because the erasure never deletes."""
    from django.db.models.deletion import PROTECT

    field = Sale._meta.get_field("customer")
    assert field.remote_field.on_delete is PROTECT
