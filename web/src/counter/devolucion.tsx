import { useState } from "react";
import type { ItemDoc, SaleDoc, SaleLineDoc } from "@/sync/registry";
import { Button } from "@/ui/button";
import { Field, Input, RadioGroup, Textarea } from "@/ui/field";
import { DOT, money as pesosOf, time } from "@/ui/format";
import { Modal } from "@/ui/panel";
import { EmptyState, RegionError } from "@/ui/states";
import { QuantityStepper } from "@/ui/stepper";
import { fromCents, pesos, toCents, totals } from "./money";
import { PAYMENT_METHODS } from "./vocabulary";

/**
 * `Devolución` — a return against a **closed** sale, whole or partial.
 *
 * **The money is stamped from the original sale line, not from today's price
 * list.** A credit note must reverse what was charged, and a price that changed
 * in between is exactly the case the sale's own record settles (§5). The stock
 * goes back **to the lot the line originally sold**, or a recall becomes
 * unanswerable (§6).
 *
 * The sale stays `closed`: a fully-returned sale is a closed sale with returns
 * against it, not a voided one.
 *
 * **The credit note a return legally requires is not issued by Botica at all** —
 * the client's own invoicing system issues it (§8). From S5 onward this record
 * is what that system renders it from, which is why every figure here is the
 * one that was charged.
 */

export interface ReturnLineDraft {
  line: SaleLineDoc;
  item: ItemDoc | undefined;
  remaining: number;
  quantity: number;
}

export function Devolucion({
  open,
  sale,
  drafts,
  onQuantity,
  onClose,
  onConfirm,
  defaultMethod,
  busy,
  failure,
  foreignLocationName,
  onVoid,
}: {
  open: boolean;
  sale: SaleDoc | null;
  drafts: ReturnLineDraft[];
  onQuantity: (lineId: string, quantity: number) => void;
  onClose: () => void;
  onConfirm: (values: { reason: string; refundMethod: string }) => void;
  defaultMethod: string;
  busy?: boolean;
  failure?: string;
  /** A return against a sale from another sede is refused in the content
   *  region naming the sede that owns it. */
  foreignLocationName?: string | null;
  /** Present only while the ticket is still inside **this cashier's own open
   *  turno** and nothing has been returned against it. Once the turno closes,
   *  only an `owner` or `admin` may void, through the office endpoint: a
   *  permissive void is how a till is robbed, and a strictly-office void means
   *  a mis-key at 20:00 waits for Monday. */
  onVoid?: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [method, setMethod] = useState(defaultMethod);
  const [touched, setTouched] = useState(false);

  // Reopening resets the form, derived during render rather than in an effect:
  // a devolución that remembered the last one's motivo would put somebody
  // else's reason on this credit note.
  const [session, setSession] = useState(false);
  if (open && !session) {
    setSession(true);
    setReason("");
    setMethod(defaultMethod);
    setTouched(false);
  } else if (!open && session) {
    setSession(false);
  }

  const returning = drafts.filter((draft) => draft.quantity > 0);
  const figures = totals(
    returning.map((draft) => ({
      quantity: draft.quantity,
      unit_price: draft.line.unit_price,
      // Prorated, so a partial return of a discounted line refunds its share
      // rather than the whole discount or none of it. **`fromCents`, because
      // `totals` reads decimal strings**: handing it a centavos integer makes
      // `toCents` scale it a hundredfold, and the customer is shown a refund a
      // hundred times the discount.
      discount: fromCents(
        Math.round(
          (toCents(draft.line.discount) * draft.quantity) /
            (draft.line.quantity || 1),
        ),
      ),
      vat_class: draft.line.vat_class,
    })),
  );
  const nothingLeft = drafts.every((draft) => draft.remaining === 0);
  const missingReason = touched && reason.trim() === "";

  return (
    <Modal
      open={open}
      title={sale ? `Devolución de ${sale.number}` : "Devolución"}
      busy={busy}
      onClose={onClose}
      footer={
        <>
          {onVoid ? (
            <Button
              size="lg"
              variant="ghost"
              className="mr-auto"
              disabled={busy}
              onClick={() => {
                setTouched(true);
                if (reason.trim()) onVoid(reason.trim());
              }}
            >
              Anular la venta
            </Button>
          ) : null}
          <Button size="lg" variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            size="lg"
            variant="primary"
            busy={busy}
            busyLabel="Registrando"
            disabled={returning.length === 0 || !!foreignLocationName}
            onClick={() => {
              setTouched(true);
              if (reason.trim())
                onConfirm({ reason: reason.trim(), refundMethod: method });
            }}
          >
            {`Devolver ${pesosOf(pesos(figures.total))}`}
          </Button>
        </>
      }
    >
      {foreignLocationName ? (
        <div className="mt-5">
          <RegionError
            title={`Esta venta es de ${foreignLocationName}.`}
            detail="Una devolución se registra en la sede que hizo la venta. Pida al mostrador de esa sede que la reciba."
          />
        </div>
      ) : nothingLeft ? (
        <div className="mt-5">
          <EmptyState
            kind="deliberate"
            title="Esta venta no tiene unidades por devolver."
            body="Todas sus líneas ya fueron devueltas."
          />
        </div>
      ) : (
        <div className="mt-5 flex flex-col gap-5">
          {sale ? (
            <p className="text-12 text-ink-body">
              {sale.number} {DOT} {time(sale.occurred_at)} {DOT}{" "}
              {pesosOf(pesos(toCents(sale.total)))}
            </p>
          ) : null}

          <ul className="flex flex-col gap-3 border-t border-hairline pt-4">
            {drafts.map((draft) => (
              <li key={draft.line.id} className="flex items-center gap-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-16 text-ink">
                    {draft.item?.name ?? "Producto"}
                  </p>
                  <p className="text-12 text-ink-note">
                    {draft.remaining === 0
                      ? "Sin unidades por devolver"
                      : `${draft.remaining} de ${draft.line.quantity} por devolver ${DOT} ${pesosOf(pesos(toCents(draft.line.unit_price)))} c/u`}
                  </p>
                </div>
                <QuantityStepper
                  label={`Unidades a devolver de ${draft.item?.name ?? "la línea"}`}
                  density="counter"
                  value={draft.quantity}
                  min={0}
                  max={draft.remaining}
                  disabled={draft.remaining === 0}
                  onCommit={(next) => onQuantity(draft.line.id, next)}
                />
              </li>
            ))}
          </ul>

          <Field
            label="Motivo"
            htmlFor="return-reason"
            error={missingReason ? "Escriba por qué se devuelve." : undefined}
            required
          >
            <Textarea
              id="return-reason"
              rows={3}
              invalid={missingReason}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>

          <RadioGroup
            legend="Medio de reembolso"
            name="refund-method"
            value={method}
            options={PAYMENT_METHODS}
            onChange={setMethod}
            className="flex-row flex-wrap gap-4"
          />

          {failure ? (
            <RegionError
              title={`No pudimos registrar la devolución de ${sale?.number ?? "la venta"}.`}
              detail={failure}
            />
          ) : null}
        </div>
      )}
    </Modal>
  );
}

/** Find a closed sale by its number, from the local store — the till's own path
 *  to yesterday's ticket, and it works with the cable out of the wall. */
export function FindSale({
  open,
  value,
  notFound,
  recent,
  onValue,
  onSubmit,
  onPick,
  onClose,
}: {
  open: boolean;
  value: string;
  notFound: boolean;
  recent: { sale: SaleDoc; label: string }[];
  onValue: (next: string) => void;
  onSubmit: () => void;
  onPick: (sale: SaleDoc) => void;
  onClose: () => void;
}) {
  return (
    <Modal
      open={open}
      title="Buscar venta"
      size="confirm"
      onClose={onClose}
      footer={
        <Button size="lg" variant="primary" onClick={onSubmit}>
          Buscar
        </Button>
      }
    >
      <div className="mt-5 flex flex-col gap-4">
        <Field
          label="Número de venta"
          htmlFor="sale-number"
          error={
            notFound
              ? `Ninguna venta de esta sede tiene ese número.`
              : undefined
          }
        >
          <Input
            id="sale-number"
            autoFocus
            className="h-control-counter text-16"
            placeholder="C1-4821"
            value={value}
            invalid={notFound}
            onChange={(event) => onValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                onSubmit();
              }
            }}
          />
        </Field>
        <div className="border-t border-hairline pt-4">
          <p className="mb-2 text-12 text-ink-label">Ventas recientes</p>
          <ul className="flex max-h-64 flex-col overflow-y-auto">
            {recent.map(({ sale, label }) => (
              <li key={sale.id}>
                <button
                  type="button"
                  onClick={() => onPick(sale)}
                  className="flex h-control-counter w-full items-center gap-3 rounded-control px-3 text-left text-14 text-ink hover:bg-chrome"
                >
                  <span className="w-24 shrink-0 tabular-nums">
                    {sale.number}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-ink-body">
                    {label}
                  </span>
                  <span className="shrink-0 tabular-nums">
                    {pesosOf(pesos(toCents(sale.total)))}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Modal>
  );
}
