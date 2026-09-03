import { useState } from "react";
import type { LotDoc, SaleLineDoc } from "@/sync/registry";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Field, Input } from "@/ui/field";
import {
  DOT,
  count as countOf,
  monthYear,
  money as pesosOf,
} from "@/ui/format";
import { Modal } from "@/ui/panel";
import { StatusDot } from "@/ui/status";
import type { LotOption } from "./local";
import { pesos, toCents } from "./money";

/**
 * The two controls a line carries beyond its quantity, and **who may reach
 * them**.
 *
 * `Lote` is a cashier's control: a box whose label disagrees with the screen is
 * a box the cashier is right about, and no setting in this stage can make the
 * override impossible (§6).
 *
 * `Precio` and `Descuento` are **not**. §2 gives a `cashier` no pricing
 * authority and the ledger's settings register gives S4 no group in which a
 * discount cap could live, so the controls are **not rendered** for one — not
 * rendered disabled (§B.8.3). An `owner` or `admin` signed in at the till gets
 * them, and the override changes that line and nothing in the price list.
 *
 * *If the pilot needs a supervisor to authorise an override without a session
 * switch*, that is a PIN elevation flow with its own audit trail, and it is not
 * in v1 — the interim answer is that the administrator signs in.
 */

export function LotPicker({
  open,
  itemName,
  options,
  selected,
  onPick,
  onClose,
}: {
  open: boolean;
  itemName: string;
  options: LotOption[];
  selected: string | null;
  /** `head` is the lot FEFO offered, so the caller can stamp the deviation on
   *  the move rather than recompute it hours later against a projection that
   *  has moved (§6). */
  onPick: (lot: LotDoc | null, head: string | null) => void;
  onClose: () => void;
}) {
  const head = options[0]?.lot?.id ?? null;
  return (
    <Modal
      open={open}
      title={`Lote de ${itemName}`}
      size="confirm"
      onClose={onClose}
      footer={
        <Button size="lg" variant="secondary" onClick={onClose}>
          Cerrar
        </Button>
      }
    >
      <div className="mt-5 flex flex-col gap-1">
        <p className="mb-2 text-14 text-ink-body">
          El primero es el que vence antes y es el que sale por defecto. Elija
          otro solo si el que tiene en la mano es ese.
        </p>
        {options.length === 0 ? (
          <p className="py-6 text-center text-14 text-ink-body">
            Esta sede no tiene lotes de este producto con existencias. La venta
            se cierra igual y la excepción llega a la oficina.
          </p>
        ) : (
          options.map((option) => (
            <button
              key={option.lot?.id ?? "sin-lote"}
              type="button"
              onClick={() => {
                onPick(option.lot, head);
                onClose();
              }}
              className={cn(
                "flex h-row-counter items-center gap-3 rounded-control px-3 text-left",
                "transition-colors duration-140 ease-out hover:bg-hover-row",
                (option.lot?.id ?? null) === selected && "bg-hover-row",
              )}
            >
              <StatusDot
                family={option.lot?.id === head ? "positive" : "neutral"}
                dot={option.lot?.id === head ? "solid" : "hollow"}
              />
              <span className="min-w-0 flex-1 truncate text-16 text-ink">
                {option.lot ? `Lote ${option.lot.lot_code}` : "Sin lote"}
              </span>
              <span className="shrink-0 text-14 text-ink-body">
                {option.lot?.expires_at
                  ? `vence ${monthYear(option.lot.expires_at)}`
                  : "sin fecha"}
              </span>
              <span className="w-16 shrink-0 text-right text-14 tabular-nums text-ink-note">
                {countOf(option.quantity)}
              </span>
            </button>
          ))
        )}
      </div>
    </Modal>
  );
}

export function PriceOverride({
  open,
  itemName,
  line,
  onApply,
  onClose,
}: {
  open: boolean;
  itemName: string;
  line: SaleLineDoc | null;
  onApply: (values: { unitPrice: number; discount: number }) => void;
  onClose: () => void;
}) {
  const [price, setPrice] = useState("");
  const [discount, setDiscount] = useState("");
  const [session, setSession] = useState(false);
  if (open !== session) {
    setSession(open);
    setPrice(line ? String(Math.round(toCents(line.unit_price) / 100)) : "");
    setDiscount(line ? String(Math.round(toCents(line.discount) / 100)) : "0");
  }

  const valid = /^\d+$/.test(price.trim()) && /^\d+$/.test(discount.trim());
  const gross = toCents(price.trim() || "0") * (line?.quantity ?? 0);
  const net = gross - toCents(discount.trim() || "0");

  return (
    <Modal
      open={open}
      title={`Precio de ${itemName}`}
      size="confirm"
      onClose={onClose}
      footer={
        <>
          <Button size="lg" variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            size="lg"
            variant="primary"
            disabled={!valid || net < 0}
            onClick={() =>
              onApply({
                unitPrice: toCents(price.trim()),
                discount: toCents(discount.trim()),
              })
            }
          >
            Aplicar
          </Button>
        </>
      }
    >
      <div className="mt-5 flex flex-col gap-4">
        <p className="text-14 text-ink-body">
          Cambia esta línea y nada más. La lista de precios no se toca.
        </p>
        <Field label="Precio por unidad" htmlFor="line-price">
          <Input
            id="line-price"
            autoFocus
            inputMode="numeric"
            className="h-control-counter text-20"
            value={price}
            onChange={(event) => setPrice(event.target.value)}
          />
        </Field>
        <Field label="Descuento de la línea" htmlFor="line-discount">
          <Input
            id="line-discount"
            inputMode="numeric"
            className="h-control-counter text-20"
            value={discount}
            onChange={(event) => setDiscount(event.target.value)}
          />
        </Field>
        <p className="text-14 text-ink">
          {line?.quantity ?? 0} × {pesosOf(pesos(toCents(price.trim() || "0")))}{" "}
          {DOT} línea {pesosOf(pesos(Math.max(0, net)))}
        </p>
      </div>
    </Modal>
  );
}
