import { useMemo, useState } from "react";
import type {
  CustomerDoc,
  ItemDoc,
  SaleDoc,
  SaleLineDoc,
} from "@/sync/registry";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Field, Input, RadioGroup } from "@/ui/field";
import { DOT, money as pesosOf, time } from "@/ui/format";
import { Modal } from "@/ui/panel";
import { RegionError } from "@/ui/states";
import { StatusDot } from "@/ui/status";
import { fromCents, pesos, toCents, totals } from "./money";
import {
  PAYMENT_METHODS,
  PAYMENT_METHOD,
  REFERENCED_METHODS,
} from "./vocabulary";

/**
 * `Cobro` — the payment flow, at counter density (§B.11).
 *
 * **`Efectivo` is preselected with the exact amount prefilled**, so the whole
 * cash path is `F2` then `Enter` — two keystrokes, and that is what makes a
 * till fast.
 *
 * **`payments.amount` for cash is the amount applied to the sale, not the
 * amount tendered.** `Recibe` and `Cambio` are display figures and are not
 * stored: the sale was paid for with its total, however many notes crossed the
 * counter.
 *
 * **No error here blocks the sale.** The dialog will not commit an unbalanced
 * split, and the recovery is always available: the sale can be committed with
 * cash for the full amount (§B.16 item 14).
 *
 * design-system.md §B.17 item 5 names this flow as the largest undrawn gap in
 * the handoff and assigns it here. What follows is an extrapolation from the
 * component layer rather than a drawing, and it should have a design pass
 * before the pilot.
 */

export interface Applied {
  method: string;
  /** In centavos. */
  amount: number;
  reference: string;
}

export interface CustomerPick {
  id: string;
  document_type: string;
  document: string;
  name: string;
}

export function Cobro({
  open,
  lines,
  items,
  customers,
  attached,
  onSearchCustomers,
  onAttach,
  onRegister,
  onCancel,
  onCommit,
  busy,
  failure,
}: {
  open: boolean;
  lines: SaleLineDoc[];
  items: Map<string, ItemDoc>;
  customers: CustomerDoc[];
  attached: CustomerPick | null;
  onSearchCustomers: (term: string) => void;
  onAttach: (customer: CustomerPick | null) => void;
  onRegister: () => void;
  onCancel: () => void;
  onCommit: (applied: Applied[], change: number) => void;
  busy?: boolean;
  failure?: string;
}) {
  const figures = totals(
    lines.map((line) => ({
      quantity: line.quantity ?? 0,
      unit_price: line.unit_price,
      discount: line.discount,
      vat_class: line.vat_class,
    })),
  );
  const total = figures.total;

  const [applied, setApplied] = useState<Applied[]>([]);
  const [tendered, setTendered] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [term, setTerm] = useState("");

  /**
   * Reopening resets the dialog: a `Cobro` that remembered the last customer's
   * split would apply it to the next customer's ticket.
   *
   * Derived during render rather than in an effect — React's own escape hatch
   * for state that follows a prop, and one render rather than the two an effect
   * would cost. **`Efectivo` is preselected with the exact amount prefilled**,
   * which is what makes the whole cash path `F2` then `Enter`.
   */
  const [session, setSession] = useState<{ open: boolean; total: number }>({
    open: false,
    total: -1,
  });
  if (open && (!session.open || session.total !== total)) {
    setSession({ open: true, total });
    setApplied([{ method: "cash", amount: total, reference: "" }]);
    setTendered(String(Math.round(total / 100)));
    setAcknowledged(false);
    setTerm("");
  } else if (!open && session.open) {
    setSession({ open: false, total: -1 });
  }

  const prescriptions = useMemo(
    () =>
      lines
        .map((line) => items.get(line.item_id ?? ""))
        .filter((item): item is ItemDoc => !!item?.requires_prescription),
    [lines, items],
  );

  const covered = applied.reduce((sum, one) => sum + one.amount, 0);
  const cashApplied = applied
    .filter((one) => one.method === "cash")
    .reduce((sum, one) => sum + one.amount, 0);
  const onlyCash = applied.length === 1 && applied[0]?.method === "cash";
  const tenderedCents = toCents(tendered);
  const change = onlyCash ? tenderedCents - total : 0;
  const short = total - covered;
  const needsAcknowledgement = prescriptions.length > 0 && !acknowledged;
  const balanced = short === 0;

  function set(index: number, patch: Partial<Applied>) {
    setApplied((rows) =>
      rows.map((row, at) => (at === index ? { ...row, ...patch } : row)),
    );
  }

  return (
    <Modal
      open={open}
      title="Cobro"
      busy={busy}
      onClose={onCancel}
      footer={
        <>
          <Button size="lg" variant="secondary" onClick={onCancel}>
            Cancelar
          </Button>
          <Button
            size="lg"
            variant="primary"
            busy={busy}
            busyLabel="Cobrando"
            disabled={!balanced || needsAcknowledgement}
            onClick={() =>
              // A method left at zero is a method the cashier did not use, and
              // the server refuses a payment of nothing.
              onCommit(
                applied.filter((one) => one.amount > 0),
                Math.max(0, change),
              )
            }
          >
            {`Cobrar ${pesosOf(pesos(total))}`}
          </Button>
        </>
      }
    >
      <div className="mt-5 flex flex-col gap-5">
        <p className="text-36 tracking-display tabular-nums text-ink">
          {pesosOf(pesos(total))}
        </p>

        {applied.map((row, index) => (
          <div key={index} className="flex flex-col gap-3">
            <RadioGroup
              legend={index === 0 ? "Medio de pago" : "Segundo medio"}
              name={`method-${index}`}
              value={row.method}
              options={PAYMENT_METHODS}
              onChange={(method) => set(index, { method })}
              className="flex-row flex-wrap gap-4"
            />
            {applied.length > 1 ? (
              <Field label="Monto" htmlFor={`amount-${index}`}>
                <Input
                  id={`amount-${index}`}
                  inputMode="numeric"
                  className="h-control-counter text-16"
                  value={String(Math.round(row.amount / 100))}
                  onChange={(event) =>
                    set(index, { amount: toCents(event.target.value) })
                  }
                />
              </Field>
            ) : null}
            {REFERENCED_METHODS.has(row.method) ? (
              <Field label="Referencia" htmlFor={`reference-${index}`} optional>
                <Input
                  id={`reference-${index}`}
                  className="h-control-counter text-16"
                  value={row.reference}
                  onChange={(event) =>
                    set(index, { reference: event.target.value })
                  }
                />
              </Field>
            ) : null}
          </div>
        ))}

        {onlyCash ? (
          <div className="flex items-end gap-6">
            <Field label="Recibe" htmlFor="tendered" className="w-48">
              <Input
                id="tendered"
                inputMode="numeric"
                className="h-control-counter text-20"
                value={tendered}
                onChange={(event) => setTendered(event.target.value)}
              />
            </Field>
            <p className="pb-6 text-20 tabular-nums text-ink">
              Cambio {pesosOf(pesos(Math.max(0, change)))}
            </p>
          </div>
        ) : null}

        {applied.length === 1 ? (
          <div>
            <Button
              size="md"
              variant="ghost"
              className="h-control-counter"
              onClick={() =>
                // **Half and half, not zero and everything.** Seeding the first
                // method at zero leaves the split arithmetically balanced while
                // one of its rows pays nothing, and the server refuses a
                // payment of zero — so the ticket would close having lost that
                // row. The cashier types over both figures anyway; what matters
                // is that neither starts at a value the sale cannot carry.
                setApplied((rows) => {
                  const part = Math.round(total / 2);
                  return [
                    { ...rows[0]!, amount: total - part },
                    { method: "debit_card", amount: part, reference: "" },
                  ];
                })
              }
            >
              Agregar otro medio
            </Button>
          </div>
        ) : null}

        {!balanced ? (
          <p className="text-12 text-critical">
            {short > 0
              ? `Faltan ${pesosOf(pesos(short))} por cubrir`
              : `Sobran ${pesosOf(pesos(-short))} aplicados a la venta`}
          </p>
        ) : null}
        {/* Cash may exceed the total — the excess is change — but only when it
            is the sole method, because a split's own arithmetic has to close. */}
        {onlyCash && tenderedCents < total ? (
          <p className="text-12 text-ink-note">
            El efectivo recibido es menor que el total. Registre el resto con
            otro medio o corrija lo recibido.
          </p>
        ) : null}
        <p className="sr-only">{fromCents(cashApplied)}</p>

        <CustomerAttach
          term={term}
          customers={customers}
          attached={attached}
          onTerm={(next) => {
            setTerm(next);
            onSearchCustomers(next);
          }}
          onAttach={onAttach}
          onRegister={onRegister}
        />

        {prescriptions.length > 0 ? (
          <div className="rounded-card bg-tint-warning px-4 py-3.5">
            <p className="flex items-center gap-[7px] text-14 text-ink">
              <StatusDot family="warning" />
              {prescriptions.length === 1
                ? `${prescriptions[0]!.name} requiere receta.`
                : `${prescriptions.length} productos de este tiquete requieren receta.`}
            </p>
            <p className="mt-1 text-12 text-ink-body">
              {prescriptions.map((item) => item.name).join(` ${DOT} `)}
            </p>
            <Button
              size="md"
              variant={acknowledged ? "ghost" : "secondary"}
              className="mt-3 h-control-counter"
              onClick={() => setAcknowledged(true)}
              disabled={acknowledged}
            >
              {acknowledged ? "Receta confirmada" : "Confirmar"}
            </Button>
          </div>
        ) : null}

        {failure ? (
          <RegionError
            title="No pudimos guardar el cobro en este equipo."
            detail={failure}
            retryLabel="Reintentar"
            onRetry={() =>
              onCommit(
                applied.filter((one) => one.amount > 0),
                Math.max(0, change),
              )
            }
          />
        ) : null}
      </div>
    </Modal>
  );
}

function CustomerAttach({
  term,
  customers,
  attached,
  onTerm,
  onAttach,
  onRegister,
}: {
  term: string;
  customers: CustomerDoc[];
  attached: CustomerPick | null;
  onTerm: (next: string) => void;
  onAttach: (customer: CustomerPick | null) => void;
  onRegister: () => void;
}) {
  return (
    <div className="border-t border-hairline pt-5">
      <Field label="Cliente" htmlFor="customer" optional>
        <Input
          id="customer"
          className="h-control-counter text-16"
          placeholder={attached ? attached.name : "Consumidor final"}
          value={term}
          onChange={(event) => onTerm(event.target.value)}
        />
      </Field>
      {attached ? (
        <p className="mt-2 flex items-center gap-3 text-14 text-ink">
          {attached.name}
          <span className="text-12 text-ink-note">
            {attached.document_type} {attached.document}
          </span>
          <Button size="sm" variant="ghost" onClick={() => onAttach(null)}>
            Quitar
          </Button>
        </p>
      ) : null}
      {term.trim() && !attached ? (
        <ul className="mt-2 flex flex-col">
          {customers.map((customer) => (
            <li key={customer.id}>
              <button
                type="button"
                className={cn(
                  "flex h-control-counter w-full items-center gap-3 rounded-control px-3 text-left",
                  "text-14 text-ink hover:bg-chrome",
                )}
                onClick={() => {
                  onAttach({
                    id: customer.id,
                    document_type: customer.document_type,
                    document: customer.document,
                    name: customer.name || "Cliente eliminado",
                  });
                  onTerm("");
                }}
              >
                <span className="min-w-0 flex-1 truncate">
                  {customer.name || "Cliente eliminado"}
                </span>
                <span className="shrink-0 text-12 text-ink-note">
                  {customer.document_type} {customer.document}
                </span>
              </button>
            </li>
          ))}
          <li>
            <Button
              size="md"
              variant="ghost"
              className="mt-1 h-control-counter"
              onClick={onRegister}
            >
              Registrar cliente
            </Button>
          </li>
        </ul>
      ) : null}
    </div>
  );
}

/**
 * `Recibo` — rendered immediately on commit, from the local sale.
 *
 * **`sales.number` is on every receipt, including one rung up entirely
 * offline, because it is the key both systems reconcile on** (§8). It is what
 * the client's invoicing system stores against its own document and what an
 * accountant matches a day of sales to a day of invoices with.
 *
 * **The fiscal block is an empty region S5 fills, and S4 says nothing about the
 * DIAN.** With no invoicing target configured — which is the default and the
 * state a demo runs in — there is no document to claim and the region is simply
 * not rendered. Claiming a document that does not exist is worse than claiming
 * nothing.
 *
 * A browser print view, and nothing else: there is no thermal printing, no
 * cash-drawer kick and no PDF in v1 (§12).
 */
export function Receipt({
  open,
  sale,
  lines,
  items,
  payments,
  locationName,
  cashierName,
  change,
  onAgain,
}: {
  open: boolean;
  sale: SaleDoc | null;
  lines: SaleLineDoc[];
  items: Map<string, ItemDoc>;
  payments: Applied[];
  locationName: string;
  cashierName: string;
  /** In centavos, and a display figure only — it is not stored. */
  change: number;
  onAgain: () => void;
}) {
  if (!sale) return null;
  const figures = totals(
    lines.map((line) => ({
      quantity: line.quantity ?? 0,
      unit_price: line.unit_price,
      discount: line.discount,
      vat_class: line.vat_class,
    })),
  );
  return (
    <Modal
      open={open}
      title={`Recibo ${sale.number}`}
      onClose={onAgain}
      footer={
        <>
          <Button size="lg" variant="secondary" onClick={() => window.print()}>
            Imprimir
          </Button>
          <Button size="lg" variant="primary" onClick={onAgain}>
            Vender otra
          </Button>
        </>
      }
    >
      <div className="mt-5 flex flex-col gap-4">
        <p className="text-12 text-ink-body">
          {locationName} {DOT} {time(sale.occurred_at)} {DOT} {cashierName}
        </p>
        <ul className="flex flex-col gap-2 border-t border-hairline pt-4">
          {lines.map((line) => (
            <li key={line.id} className="flex items-baseline gap-3 text-14">
              <span className="min-w-0 flex-1 truncate text-ink">
                {items.get(line.item_id ?? "")?.name ?? "Producto"}
              </span>
              <span className="shrink-0 text-12 tabular-nums text-ink-note">
                {line.quantity} × {pesosOf(pesos(toCents(line.unit_price)))}
              </span>
              <span className="w-24 shrink-0 text-right tabular-nums text-ink">
                {pesosOf(
                  pesos(
                    toCents(line.unit_price) * (line.quantity ?? 0) -
                      toCents(line.discount),
                  ),
                )}
              </span>
            </li>
          ))}
        </ul>
        <div className="flex flex-col gap-1.5 border-t border-hairline pt-4 text-14">
          <Row label="Subtotal" value={figures.subtotal} />
          <Row label="Descuento" value={figures.discount} />
          <Row label="Total" value={figures.total} strong />
        </div>
        <div className="flex flex-col gap-1.5 border-t border-hairline pt-4 text-14">
          {payments.map((payment, index) => (
            <Row
              key={index}
              label={PAYMENT_METHOD[payment.method] ?? payment.method}
              value={payment.amount}
            />
          ))}
          {change > 0 ? <Row label="Cambio" value={change} /> : null}
        </div>
      </div>
    </Modal>
  );
}

function Row({
  label,
  value,
  strong,
}: {
  label: string;
  value: number;
  strong?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between">
      <span className={cn("text-12", strong ? "text-ink" : "text-ink-body")}>
        {label}
      </span>
      <span
        className={cn(
          "tabular-nums text-ink",
          strong ? "text-20 tracking-display" : "text-14",
        )}
      >
        {pesosOf(pesos(value))}
      </span>
    </div>
  );
}
