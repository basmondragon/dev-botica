import { forwardRef } from "react";
import type { ItemDoc } from "@/sync/registry";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { CONTROL_BASE } from "@/ui/field";
import { DOT, count as countOf, money as pesosOf } from "@/ui/format";
import { StatusDot } from "@/ui/status";
import { StockBar } from "@/ui/tile";
import { pesos } from "./money";
import { stockState } from "./vocabulary";

/**
 * The till's left column until S8 arrives, and the capture field at its top.
 *
 * **This same list re-renders as an L3 overlay anchored under the capture field
 * when S8 takes the column** — the transcript, the recommendation and the
 * suggestion cards move in, and the handoff's own header `Buscar producto`
 * button is what implies the overlay. It is built to render in both positions
 * from day one, which is why the list is a component taking its own geometry
 * rather than a block inside the route.
 *
 * *If that is wrong* — if the pilot's cashiers want the search list permanently
 * visible beside the assistant — the fix is a two-region left column with
 * shorter suggestion cards, which is a layout change inside one route and
 * touches no endpoint and no table.
 */

export interface Hit {
  item: ItemDoc;
  /** In centavos. Null where the item has no price in force at this sede, which
   *  is not zero: the line cannot be added and the row says so. */
  price: number | null;
  quantity: number;
  reorderPoint: number | null;
  manufacturer: string;
  /** §B.9.2 tier 2 · another sede's stock, shown only when this one is out.
   *  It is the one figure on the till that is **not** authoritative, so it
   *  carries the hollow grey dot and its own reading. */
  elsewhere: { locationName: string; quantity: number } | null;
}

export const CaptureField = forwardRef<
  HTMLInputElement,
  {
    value: string;
    invalid?: boolean;
    onChange: (next: string) => void;
    onEnter: () => void;
    onEscape: () => void;
  }
>(function CaptureField({ value, invalid, onChange, onEnter, onEscape }, ref) {
  return (
    <input
      ref={ref}
      value={value}
      autoFocus
      // A scanner emits its buffer as keystrokes; a browser that offered a
      // history dropdown over the field would put a listbox between the burst
      // and the ticket.
      autoComplete="off"
      spellCheck={false}
      aria-label="Escanee o busque un producto"
      aria-invalid={invalid || undefined}
      placeholder="Escanee un producto o escriba para buscar"
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          onEnter();
        }
        if (event.key === "Escape") {
          event.preventDefault();
          onEscape();
        }
      }}
      className={cn(
        CONTROL_BASE,
        "h-control-counter w-full px-3.5 text-16",
        invalid && "border-critical",
      )}
    />
  );
});

export function SearchColumn({
  term,
  hits,
  referenceCount,
  locationName,
  onAdd,
  onClear,
  field,
}: {
  term: string;
  hits: Hit[];
  referenceCount: number;
  locationName: string;
  onAdd: (hit: Hit) => void;
  onClear: () => void;
  field: React.ReactNode;
}) {
  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-panel border border-edge-soft bg-surface shadow-plane">
      <header className="flex h-10 shrink-0 items-center gap-4 border-b border-hairline bg-chrome px-5">
        <h2 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
          Buscar producto
        </h2>
        {term.trim() ? (
          <span className="ml-auto text-11 text-ink-note">
            {countOf(hits.length)}{" "}
            {hits.length === 1 ? "resultado" : "resultados"}
          </span>
        ) : null}
      </header>

      <div className="shrink-0 border-b border-hairline px-5 py-4">{field}</div>

      <SearchResults
        term={term}
        hits={hits}
        referenceCount={referenceCount}
        locationName={locationName}
        onAdd={onAdd}
        onClear={onClear}
      />
    </section>
  );
}

/**
 * The list itself, and its two empty states.
 *
 * **It is a component rather than a block inside the column** because it
 * renders in two places: the full-height left column before S8 lands and
 * wherever `enabled` is false, and an L3 overlay anchored under the capture
 * field once the assistant has taken that column. Neither position knows about
 * the other; both hand it its own geometry.
 */
export function SearchResults({
  term,
  hits,
  referenceCount,
  locationName,
  onAdd,
  onClear,
}: {
  term: string;
  hits: Hit[];
  referenceCount: number;
  locationName: string;
  onAdd: (hit: Hit) => void;
  onClear: () => void;
}) {
  if (term.trim() === "") {
    return (
      <Deliberate referenceCount={referenceCount} locationName={locationName} />
    );
  }
  if (hits.length === 0) return <Filtered term={term} onClear={onClear} />;
  return (
    <ul className="min-h-0 flex-1 overflow-y-auto">
      {hits.map((hit) => (
        <Row key={hit.item.id} hit={hit} onAdd={onAdd} />
      ))}
    </ul>
  );
}

/** §B.10.2 · the deliberately-empty kind: it carries **no action**, because
 *  there is nothing to do but scan. */
function Deliberate({
  referenceCount,
  locationName,
}: {
  referenceCount: number;
  locationName: string;
}) {
  return (
    <div className="mx-auto flex max-w-[420px] flex-col items-center px-5 py-12 text-center">
      <p className="text-16 text-ink">
        Escanee un producto o escriba para buscar
      </p>
      <p className="mt-2 text-14 text-ink-body">
        {countOf(referenceCount)} referencias disponibles en {locationName}.
      </p>
    </div>
  );
}

function Filtered({ term, onClear }: { term: string; onClear: () => void }) {
  return (
    <div className="mx-auto flex max-w-[420px] flex-col items-center px-5 py-12 text-center">
      <p className="text-16 text-ink">
        Ningún producto coincide con «{term.trim()}»
      </p>
      <Button size="md" variant="secondary" className="mt-5" onClick={onClear}>
        Limpiar búsqueda
      </Button>
    </div>
  );
}

/** Where the bar sits: full at four times the reorder point, and where no
 *  threshold exists there is no denominator, so the bar reads empty and the
 *  figure carries the answer on its own (§B.12.2). */
function fill(hit: Hit): number {
  if (hit.quantity <= 0) return 0;
  const ceiling = hit.reorderPoint ? hit.reorderPoint * 4 : 0;
  if (!ceiling) return 0;
  return Math.min(100, Math.round((hit.quantity / ceiling) * 100));
}

function Row({ hit, onAdd }: { hit: Hit; onAdd: (hit: Hit) => void }) {
  const state = stockState(hit.quantity, hit.reorderPoint);
  return (
    <li className="flex h-row-counter items-center gap-4 border-b border-hairline px-5 last:border-b-0">
      <div className="min-w-0 flex-1">
        <p className="truncate text-16 text-ink">{hit.item.name}</p>
        <p className="truncate text-14 text-ink-body">
          {[hit.manufacturer, hit.item.presentation]
            .filter(Boolean)
            .join(` ${DOT} `)}
        </p>
      </div>

      <div className="w-32 shrink-0">
        {/* §A.18.1 · the bar and its figure. **Normalised against the reorder
            point rather than against the page**, because on a till the question
            is "is there enough of this one", not "how does it compare to the
            other twenty-four rows on screen". */}
        <StockBar
          fill={fill(hit)}
          figure={countOf(hit.quantity)}
          label={`${hit.quantity} unidades en esta sede`}
        />
      </div>

      {/* §B.7.3 · a dot and a label, not the tinted badge: one badge column per
          surface, and on the till it is the ticket's own line flags. */}
      <span className="inline-flex w-44 shrink-0 flex-col gap-0.5">
        <span className="inline-flex items-center gap-[7px] text-12 text-ink-note">
          <StatusDot family={state.family} />
          {state.label}
        </span>
        {/* §B.9.2 tier 2 · **the one figure here that is not this sede's own.**
            It carries the hollow dot and names the sede, because a cashier
            holding a customer needs to know where the box is, and a number
            from another shop is not a number this till is authoritative
            about. */}
        {hit.elsewhere ? (
          <span className="inline-flex items-center gap-[7px] text-11 text-ink-note">
            <span
              aria-hidden
              className="inline-block size-1 shrink-0 rounded-pill border border-ink-disabled"
            />
            {`hay ${countOf(hit.elsewhere.quantity)} en ${hit.elsewhere.locationName}`}
          </span>
        ) : null}
      </span>

      <span className="w-24 shrink-0 text-right text-16 tabular-nums text-ink">
        {hit.price === null ? "—" : pesosOf(pesos(hit.price))}
      </span>

      <Button
        size="md"
        variant="secondary"
        className="h-control-counter shrink-0"
        disabled={hit.price === null}
        onClick={() => onAdd(hit)}
      >
        Agregar
      </Button>
    </li>
  );
}
