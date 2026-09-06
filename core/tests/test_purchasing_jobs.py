"""The five jobs, checked on what each wrote rather than on whether it finished.

**This is the check whose absence costs the most, because its failure makes no
noise** (rule 6, context three). Under `FORCE ROW LEVEL SECURITY` an unpinned
connection reads and writes zero rows, so a job that took its pin from anywhere
but its own payload completes, logs a success, and writes nothing at all.

The gateway half is here too, and its whole point is a negative: with the model
unreachable, generation still produces an order with quantities on every line,
`Por qué` shows the deterministic strings, and **nothing blocks and no error
dialog appears** (§10).
"""

from decimal import Decimal

import uuid

import pytest
from django.core import mail as django_mail
from django.utils import timezone

from core import gateway
from core.models import (
    DemandForecast,
    ForecastBasis,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    Supplier,
    Tenant,
)
from core.purchasing import jobs, orders as order_service, reason_text
from core.purchasing import settings as purchasing_settings
from core.tenancy import pin_tenant
from core.tests.test_purchasing_api import build_order, sent_order
from core.tests.test_purchasing_forecast import link, refresh, sell, stock, supplier
from core.tests.test_sync_pull import make_item

pytestmark = pytest.mark.django_db


def test_the_refresh_job_writes_into_the_tenant_it_was_given(
    tenant_a, tenant_b, sede_a
):
    """Assert on rows written. A job that completes having written nothing is
    the defect, not the pass."""
    item = make_item(tenant_a, "Losartán 50 mg × 30", tracks_lots=False)
    stock(tenant_a, sede_a, item, 40, days_back=210)

    jobs.forecast_refresh(
        tenant_id=str(tenant_a.id),
        location_id=str(sede_a.id),
        run_date=timezone.localdate().isoformat(),
    )
    assert DemandForecast.objects.filter(tenant=tenant_a).count() == 1
    assert DemandForecast.objects.filter(tenant=tenant_b).count() == 0


def test_the_generation_job_produces_the_order_and_nothing_elsewhere(
    tenant_a, tenant_b, sede_a
):
    item = make_item(tenant_a, "Acetaminofén 500 mg × 100", tracks_lots=False)
    stock(tenant_a, sede_a, item, 20, days_back=210)
    link(tenant_a, item, supplier(tenant_a))
    for week in range(1, 21):
        sell(tenant_a, sede_a, item, weeks_back=week, units=30)
    refresh(tenant_a, sede_a)

    report = jobs.order_generate(
        tenant_id=str(tenant_a.id),
        location_id=str(sede_a.id),
        run_date=timezone.localdate().isoformat(),
    )
    assert report["orders"] == 1
    assert PurchaseOrder.objects.filter(tenant=tenant_a).count() == 1
    assert PurchaseOrder.objects.filter(tenant=tenant_b).count() == 0


def test_a_job_payload_with_no_tenant_fails_loudly(sede_a):
    """A job that completed having written nothing is the defect, so the absent
    pin has to be an exception rather than a quiet no-op."""
    with pytest.raises(TypeError):
        jobs.forecast_refresh(location_id=str(sede_a.id), run_date="2026-09-04")


def test_the_reason_text_job_is_a_no_op_when_the_gateway_is_unreachable(
    tenant_a, sede_a, settings
):
    """Acceptance 7 · **with the model gateway unreachable, generation still
    produces an order with quantities on every line**, `Por qué` shows the
    deterministic strings, and nothing blocks."""
    settings.BOTICA_GATEWAY_BASE_URL = ""
    settings.BOTICA_GATEWAY_API_KEY = ""
    _item, _seller, order = build_order(tenant_a, sede_a)

    landed = jobs.order_reason_text(
        tenant_id=str(tenant_a.id), purchase_order_id=str(order.id)
    )
    assert landed == 0
    with pin_tenant(tenant_a.id):
        lines = list(PurchaseOrderLine.objects.filter(purchase_order=order))
        assert lines
        assert all(line.reason == "" for line in lines)
        assert all(line.reason_code for line in lines)
        assert all(line.approved_quantity is not None for line in lines)


def test_the_stages_own_switch_suppresses_the_call(tenant_a, sede_a, settings):
    """`reason_text_enabled` gates this stage's call and nothing else."""
    settings.BOTICA_GATEWAY_BASE_URL = "https://example.invalid/v1"
    settings.BOTICA_GATEWAY_API_KEY = "not-a-real-key"
    _item, _seller, order = build_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        purchasing_settings.write(
            Tenant.objects.get(id=tenant_a.id), {"reason_text_enabled": False}
        )
    assert (
        jobs.order_reason_text(
            tenant_id=str(tenant_a.id), purchase_order_id=str(order.id)
        )
        == 0
    )


def test_the_assistants_kill_switch_stops_the_call_too(tenant_a, sede_a, settings):
    """S8's `assistant` group holds the shared kill switch and the per-tenant
    spend cap; either being off suppresses the call.

    **The switch is `model_enabled` and not `enabled`** -- S8 landed and settled
    which of its two booleans this is: `enabled` removes the assistant column
    from Mostrador and says nothing about whether a vendor may be called. It
    ships **off**, because §11.3 is unanswered and a *no* costs no code.

    The cap is now a read over the month's own `assistant_queries.cost_usd`
    rather than a figure somebody has to keep up to date, which is what makes it
    one cap rather than two.
    """
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from core.assistant import settings as assistant_settings
    from core.models import AssistantMode, AssistantQuery

    settings.BOTICA_GATEWAY_BASE_URL = "https://example.invalid/v1"
    settings.BOTICA_GATEWAY_API_KEY = "not-a-real-key"
    with pin_tenant(tenant_a.id):
        tenant = Tenant.objects.get(id=tenant_a.id)
        assert gateway.enabled_for(tenant) is False

        assistant_settings.write(
            tenant, {"model_enabled": True, "monthly_spend_cap_usd": 1.0}
        )
        tenant.refresh_from_db()
        assert gateway.enabled_for(tenant) is True

        # The column switch is a different switch, and it does not gate this.
        assistant_settings.write(tenant, {"enabled": False})
        tenant.refresh_from_db()
        assert gateway.enabled_for(tenant) is True

        AssistantQuery.objects.create(
            tenant=tenant_a,
            location=sede_a,
            client_uuid=uuid.uuid4(),
            mode=AssistantMode.MODEL,
            cost_usd=Decimal("1.20"),
            occurred_at=timezone.now(),
            recorded_at=timezone.now(),
        )
        tenant.refresh_from_db()
        assert gateway.enabled_for(tenant) is False

        # Last month's spend is last month's.
        AssistantQuery.objects.update(recorded_at=timezone.now() - timedelta(days=60))
        assert gateway.enabled_for(tenant) is True


def test_only_learned_lines_are_ever_sent_to_the_model(tenant_a, sede_a):
    """§1 · **a language model asked to dress up *we have no history* writes a
    finding**, and shipping an invented finding is the one thing this stage must
    never do. The filter is in the query, and again in the table's own CHECK."""
    from core.models import PolicySource, StockPolicy

    measured = make_item(tenant_a, "Con venta", tracks_lots=False)
    thin = make_item(tenant_a, "Sin venta", tracks_lots=False)
    stock(tenant_a, sede_a, measured, 30, days_back=210)
    stock(tenant_a, sede_a, thin, 2, days_back=210)
    StockPolicy.objects.create(
        tenant=tenant_a,
        item=thin,
        location=sede_a,
        source=PolicySource.MANUAL,
        reorder_point=10,
        max_quantity=60,
    )
    seller = supplier(tenant_a)
    link(tenant_a, measured, seller)
    link(tenant_a, thin, seller)
    for week in range(1, 21):
        sell(tenant_a, sede_a, measured, weeks_back=week, units=25)
    refresh(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        order = order_service.generate(tenant_a.id, sede_a.id)[0]
        facts = jobs._facts(order)
        sent = {fact.line_id for fact in facts}
        learned = {
            str(line.id)
            for line in PurchaseOrderLine.objects.filter(
                purchase_order=order, basis=ForecastBasis.LEARNED
            )
        }
    assert sent == learned


def test_the_table_refuses_prose_on_a_line_that_has_no_history(tenant_a, sede_a):
    """The other half of the same guarantee, and it is structural: the database
    refuses a `reason` on anything but a `learned` line."""
    from django.db import IntegrityError, transaction
    from core.models import PolicySource, StockPolicy

    item = make_item(tenant_a, "Sin histórico", tracks_lots=False)
    stock(tenant_a, sede_a, item, 2)
    StockPolicy.objects.create(
        tenant=tenant_a,
        item=item,
        location=sede_a,
        source=PolicySource.MANUAL,
        reorder_point=10,
        max_quantity=60,
    )
    link(tenant_a, item, supplier(tenant_a))
    refresh(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        order = order_service.generate(tenant_a.id, sede_a.id)[0]
        line = order.lines.get()
        with pytest.raises(IntegrityError), transaction.atomic():
            PurchaseOrderLine.objects.filter(id=line.id).update(
                reason="Sube con la temporada de polen"
            )


def test_the_dispatch_job_sends_the_order_and_marks_it_sent(tenant_a, sede_a, settings):
    """Acceptance 5 · the supplier receives the order by email with the lines
    attached, and the badge moves to **Enviada al proveedor**."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    django_mail.outbox = []
    _item, seller, order = build_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        Supplier.objects.filter(id=seller.id).update(contact="pedidos@coopidrogas.co")
        order_service.approve(order, actor=None)

    jobs.order_dispatch(tenant_id=str(tenant_a.id), purchase_order_id=str(order.id))
    with pin_tenant(tenant_a.id):
        assert PurchaseOrder.objects.get(id=order.id).status == (
            PurchaseOrderStatus.SENT
        )
    assert django_mail.outbox
    assert "orden de compra" in django_mail.outbox[0].subject


def test_a_supplier_with_no_address_leaves_the_reason_on_the_order(
    tenant_a, sede_a, settings
):
    """§B.10.3 · the region-scope error names the reason and the retry count
    beside `[Reintentar ahora]` and `[Marcar como enviada]`, and a screen cannot
    name what nothing stored."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _item, _seller, order = build_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        order_service.approve(order, actor=None)

    assert (
        jobs.order_dispatch(tenant_id=str(tenant_a.id), purchase_order_id=str(order.id))
        == "no_address"
    )
    with pin_tenant(tenant_a.id):
        fresh = PurchaseOrder.objects.get(id=order.id)
    assert fresh.status == PurchaseOrderStatus.APPROVED
    assert fresh.dispatch_attempts == 1
    assert "correo registrado" in fresh.last_dispatch_error


def test_the_dispatch_job_never_re_sends_an_order_that_already_left(
    tenant_a, sede_a, settings
):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    django_mail.outbox = []
    _item, seller, order = build_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        Supplier.objects.filter(id=seller.id).update(contact="pedidos@coopidrogas.co")
        order_service.approve(order, actor=None)
        order_service.mark_sent(order)

    jobs.order_dispatch(tenant_id=str(tenant_a.id), purchase_order_id=str(order.id))
    assert django_mail.outbox == []


def test_the_lead_time_job_moves_the_supplier_and_nobody_else(
    tenant_a, tenant_b, sede_a
):
    _item, seller, order = sent_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        PurchaseOrder.objects.filter(id=order.id).update(
            sent_at=timezone.now() - timezone.timedelta(days=6)
        )
        from core.purchasing import receiving

        receipt = receiving.open_against(order)
        for line in receipt.lines.all():
            line.lot_code = "L-1"
            line.expires_at = timezone.localdate() + timezone.timedelta(days=500)
            line.save()
        receiving.confirm(receipt)

    jobs.lead_time_refresh(
        tenant_id=str(tenant_a.id),
        goods_receipt_id=str(receipt.id),
        supplier_id=str(seller.id),
    )
    assert Supplier.objects.get(id=seller.id).lead_time_days == 6
    assert Supplier.objects.filter(tenant=tenant_b).count() == 0


def test_the_prompt_carries_the_whole_order_and_reads_back_one_clause_per_line(
    tenant_a, sede_a, monkeypatch
):
    """What the call buys is a sentence the arithmetic cannot write. The parser
    survives a model that fences its JSON, because losing an order's prose to a
    code block is not a failure worth having."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        facts = jobs._facts(order)
        assert facts

        def answer(*, tenant, prompt, system, **extra):
            del tenant, system, extra
            assert facts[0].item in prompt
            body = ", ".join(f'"{fact.line_id}": "Rotación estable"' for fact in facts)
            return {"text": "```json\n{" + body + "}\n```", "usage": {}}

        monkeypatch.setattr(gateway, "complete", answer)
        tenant = Tenant.objects.get(id=tenant_a.id)
        assert reason_text.write(order, tenant=tenant, facts=facts) == len(facts)
        assert PurchaseOrderLine.objects.filter(
            purchase_order=order, reason="Rotación estable"
        ).count() == len(facts)


def test_a_retry_replaces_prose_and_never_a_number(tenant_a, sede_a, monkeypatch):
    _item, _seller, order = build_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        facts = jobs._facts(order)
        quantities = {
            str(line.id): line.approved_quantity
            for line in PurchaseOrderLine.objects.filter(purchase_order=order)
        }

        monkeypatch.setattr(
            gateway,
            "complete",
            lambda **kwargs: {
                "text": "{"
                + ", ".join(f'"{fact.line_id}": "Otra frase"' for fact in facts)
                + "}",
                "usage": {},
            },
        )
        tenant = Tenant.objects.get(id=tenant_a.id)
        reason_text.write(order, tenant=tenant, facts=facts)
        reason_text.write(order, tenant=tenant, facts=facts)
        after = {
            str(line.id): line.approved_quantity
            for line in PurchaseOrderLine.objects.filter(purchase_order=order)
        }
    assert after == quantities


def test_the_counterfactual_is_absent_where_there_is_nothing_to_compare(
    tenant_a, sede_a
):
    """*UI* · **a difference of zero would read as *the model saved you
    nothing*** rather than *there is nothing here to compare*."""
    from core.models import PolicySource, StockPolicy

    item = make_item(tenant_a, "Sin histórico", tracks_lots=False)
    stock(tenant_a, sede_a, item, 2)
    StockPolicy.objects.create(
        tenant=tenant_a,
        item=item,
        location=sede_a,
        source=PolicySource.MANUAL,
        reorder_point=10,
        max_quantity=60,
    )
    link(tenant_a, item, supplier(tenant_a))
    refresh(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        order = order_service.generate(tenant_a.id, sede_a.id)[0]
        options = purchasing_settings.read(Tenant.objects.get(id=tenant_a.id))
        assert (
            order_service.counterfactual(tenant_a.id, sede_a.id, order, options=options)
            is None
        )


def test_the_order_total_is_the_sum_of_its_approved_lines(tenant_a, sede_a):
    _item, _seller, order = build_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        expected = sum(
            (line.unit_cost or Decimal("0")) * line.approved_quantity
            for line in PurchaseOrderLine.objects.filter(purchase_order=order)
        )
        assert PurchaseOrder.objects.get(id=order.id).total == expected
