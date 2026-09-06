import type { ItemDoc, LotDoc, SaleLineDoc } from "@/sync/registry";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { DOT, monthYear, money as pesosOf } from "@/ui/format";
import { SkeletonBar } from "@/ui/states";
import { StatusDot, type Family } from "@/ui/status";
import { QuantityStepper } from "@/ui/stepper";
import { pesos, toCents, totals, type Totals } from "./money";
import { LINE_FLAGS } from "./vocabulary";

/**
 * §A.19.4 · the counter panel, at counter density (§B.11).
 *
 * 420px rather than the drawn 380px, which is the one geometric change counter
 * density makes to this component; the type promotes one step on the existing
 * ladder and nothing else moves. The totals are pinned to the foot and the
 * footer carries the full-width 52px primary `Cobrar` and the centred note.
 */

export interface TicketLine {
  line: SaleLineDoc;
  item: ItemDoc | undefined;
  lot: LotDoc | undefined;
}

export function TicketPanel({
  lines,
  average,
  locationName,
  canPrice,
  onQuantity,
  onRemove,
  onLot,
  onPrice,
  onCobrar,
  onFocusLine,
  busy,
  loading,
}: {
  lines: TicketLine[];
  /** In centavos, or null where the till cannot answer (§B.9.2 tier 3). */
  average: number | null;
  locationName: string;
  /** §2 gives a `cashier` no pricing authority, so the price and discount
   *  control is **not rendered** for one — not rendered disabled (§B.8.3). The
   *  lot control is a cashier's and is always there. */
  canPrice: boolean;
  onQuantity: (line: SaleLineDoc, quantity: number) => void;
  onRemove: (line: SaleLineDoc) => void;
  onLot: (entry: TicketLine) => void;
  onPrice: (entry: TicketLine) => void;
  onCobrar: () => void;
  onFocusLine: () => void;
  busy?: boolean;
  /** §B.10.1 · first paint, before the local store has answered. */
  loading?: boolean;
}) {
  const figures: Totals = totals(
    lines.map(({ line }) => ({
      quantity: line.quantity ?? 0,
      unit_price: line.unit_price,
      discount: line.discount,
      vat_class: line.vat_class,
    })),
  );

  return (
    <aside className="flex w-[420px] shrink-0 flex-col overflow-hidden rounded-panel border border-edge-soft bg-surface shadow-plane">
      <header className="flex h-10 shrink-0 items-center gap-4 border-b border-hairline bg-chrome px-5">
        <h2 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
          Venta en curso
        </h2>
        <span className="ml-auto text-11 text-ink-note">
          {figures.units} {figures.units === 1 ? "ítem" : "ítems"}
        </span>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto px-5 py-4">
        {loading ? (
          /* §B.10.1 · a geometry-matched skeleton, at the real heights, with a
             real totals block below it — never a spinner and never a blank
             panel. Three lines, because that is what the handoff draws. */
          <>
            {[0, 1, 2].map((row) => (
              <div key={row} className="flex flex-col gap-1.5">
                <div className="flex items-baseline gap-3">
                  <SkeletonBar className="h-3 w-5" />
                  <SkeletonBar className="h-3 flex-1" />
                  <SkeletonBar className="h-3 w-16" />
                </div>
                <div className="pl-8">
                  <SkeletonBar className="h-2.5 w-24" />
                </div>
              </div>
            ))}
          </>
        ) : lines.length === 0 ? (
          <p className="py-8 text-center text-14 text-ink-body">
            Escanee un producto o escríbalo para empezar el tiquete.
          </p>
        ) : (
          lines.map((entry, index) => (
            <Line
              key={entry.line.id}
              index={index + 1}
              entry={entry}
              canPrice={canPrice}
              onQuantity={onQuantity}
              onRemove={onRemove}
              onLot={onLot}
              onPrice={onPrice}
              onFocusLine={onFocusLine}
            />
          ))
        )}
      </div>

      <div className="shrink-0 border-t border-hairline px-5 pb-4 pt-4">
        <Total label="Subtotal" value={figures.subtotal} />
        <Total label="Descuento" value={figures.discount} className="mt-2.5" />
        <div className="mt-2.5 flex items-baseline justify-between">
          <span className="text-12 text-ink-body">Total</span>
          <span className="text-36 tracking-display tabular-nums text-ink">
            {pesosOf(pesos(figures.total))}
          </span>
        </div>
      </div>

      <footer className="flex shrink-0 flex-col gap-2 border-t border-hairline bg-chrome px-5 py-4">
        <Button
          variant="primary"
          size="lg"
          className="h-primary-counter w-full"
          disabled={lines.length === 0}
          busy={busy}
          busyLabel="Cobrando"
          onClick={onCobrar}
        >
          Cobrar
        </Button>
        <p className="text-center text-11 text-ink-note">
          {average === null
            ? `Ticket promedio del punto: — ${DOT} sin datos suficientes`
            : `Ticket promedio del punto: ${pesosOf(pesos(average))}`}
        </p>
        <p className="sr-only">{locationName}</p>
      </footer>
    </aside>
  );
}

function Total({
  label,
  value,
  className,
}: {
  label: string;
  value: number;
  className?: string;
}) {
  return (
    <div className={cn("flex items-baseline justify-between", className)}>
      <span className="text-12 text-ink-body">{label}</span>
      <span className="text-12 tabular-nums text-ink">
        {pesosOf(pesos(value))}
      </span>
    </div>
  );
}

function Line({
  index,
  entry,
  canPrice,
  onQuantity,
  onRemove,
  onLot,
  onPrice,
  onFocusLine,
}: {
  index: number;
  entry: TicketLine;
  canPrice: boolean;
  onQuantity: (line: SaleLineDoc, quantity: number) => void;
  onRemove: (line: SaleLineDoc) => void;
  onLot: (entry: TicketLine) => void;
  onPrice: (entry: TicketLine) => void;
  onFocusLine: () => void;
}) {
  const { line, item, lot } = entry;
  const quantity = line.quantity ?? 0;
  const unit = toCents(line.unit_price);
  const pack = item?.units_per_pack ?? 1;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-3">
        <span className="w-5 shrink-0 text-12 tabular-nums text-ink-disabled">
          {index}
        </span>
        <p className="min-w-0 flex-1 truncate text-16 text-ink">
          {item?.name ?? "Producto"}
        </p>
        <span className="shrink-0 text-16 tabular-nums text-ink">
          {pesosOf(pesos(unit * quantity - toCents(line.discount)))}
        </span>
      </div>

      <div className="flex items-center gap-3 pl-8">
        <span className="text-12 tabular-nums text-ink-note">
          {quantity} × {pesosOf(pesos(unit))}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {item?.splittable && pack > 1 ? (
            <Button
              size="sm"
              variant="ghost"
              className="h-control-counter"
              onClick={() => {
                onQuantity(line, quantity + pack);
                onFocusLine();
              }}
            >
              {`+ 1 ${item.unit === "unidad" ? "caja" : item.unit} (${pack})`}
            </Button>
          ) : null}
          <QuantityStepper
            label={`Cantidad de ${item?.name ?? "la línea"}`}
            density="counter"
            value={quantity}
            min={0}
            onCommit={(next) => {
              if (next <= 0) onRemove(line);
              else onQuantity(line, next);
              onFocusLine();
            }}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 pl-8">
        {/* §A.19.4 · **the one thing S8 adds to this panel**, and it is a
            reading rather than a control: the line came off a suggestion card,
            which is what `sale_lines.from_suggestion` records and what the
            Panel's acceptance tile rests on. */}
        {line.from_suggestion ? (
          <span className="text-11 text-ink-note">{DOT} sugerido</span>
        ) : null}
        {item?.requires_prescription ? (
          <Flag {...LINE_FLAGS.prescription} />
        ) : null}
        {item?.invima_status === "expired" ? (
          <Flag {...LINE_FLAGS.expiredRegistration} />
        ) : null}
        {/* **The lot control is a cashier's.** A box whose label disagrees with
            the screen is a box the cashier is right about, and no setting in
            this stage can make the override impossible (§6). */}
        {item?.tracks_lots ? (
          <button
            type="button"
            onClick={() => onLot(entry)}
            className="inline-flex items-center gap-[7px] rounded-control text-12 text-ink-note hover:text-ink"
          >
            <StatusDot family="neutral" dot="hollow" />
            {lot
              ? `Lote ${lot.lot_code}${
                  lot.expires_at
                    ? ` ${DOT} vence ${monthYear(lot.expires_at)}`
                    : ""
                }`
              : "Sin lote en esta sede"}
          </button>
        ) : null}
        {/* §B.8.3 · **absent for a cashier, not disabled.** §2 gives the role
            no pricing authority, which is why the drawn ticket reads
            `Descuento $0`. */}
        {canPrice ? (
          <button
            type="button"
            onClick={() => onPrice(entry)}
            className="inline-flex items-center gap-[7px] rounded-control text-12 text-ink-note hover:text-ink"
          >
            <StatusDot family="info" dot="hollow" />
            {toCents(line.discount) > 0
              ? `Descuento ${pesosOf(pesos(toCents(line.discount)))}`
              : "Precio y descuento"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** §B.7.3 · a dot and a label, not the tinted badge: the ticket's flags are the
 *  one badge column this surface has, and two of them on one line is a status
 *  shown incidentally. */
function Flag({ label, family }: { label: string; family: Family }) {
  return (
    <span className="inline-flex items-center gap-[7px] text-12 text-ink-note">
      <StatusDot family={family} />
      {label}
    </span>
  );
}
