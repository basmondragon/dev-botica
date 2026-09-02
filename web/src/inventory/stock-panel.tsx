import { useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  useAvailability,
  useLot,
  useMoves,
  useSavePolicies,
  type StockRow,
} from "@/api/inventory";
import type { Me } from "@/api/queries";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { count, money, monthYear, since } from "@/ui/format";
import { RecordPanel } from "@/ui/panel";
import { Badge, INVIMA_STATUS, StatusLine } from "@/ui/status";
import { BAR, RegionError, SkeletonBar } from "@/ui/states";
import { StockBar } from "@/ui/tile";
import { useToast } from "@/ui/toast";
import { MovementDialog } from "./movement-dialog";
import {
  MOVE_REASON,
  MOVE_TYPE,
  stateBadge,
  stockoutClause,
} from "./vocabulary";

/** The record panel's own cap on move history (§B.8.5, S3's *UI*). */
const HISTORY = 50;

/**
 * §B.8.5 · the 440px record panel for one `(item, sede, lote)` row.
 *
 * It **pushes** the content region rather than overlaying it and takes no
 * scrim, so the table behind it stays navigable -- which is the whole point of
 * `j`/`k`.
 *
 * The sections, in the order the stage document fixes: the state and its
 * reading, the thresholds with **`source` beside them** -- the one column that
 * tells a regente which numbers are theirs and which the model wrote -- the
 * lot's expiry, supplier, cost and sanitary registration, the item's
 * `invima_status`, the quantity at every other sede, the last fifty moves, and
 * a link to the full lot trace.
 */
export function StockPanel({
  rowId,
  row,
  me,
  onClose,
}: {
  rowId: string;
  row: StockRow | undefined;
  me: Me;
  onClose: () => void;
}) {
  const elevated = me.role !== "cashier";
  const [movement, setMovement] = useState(false);
  const lot = useLot(row?.lot_id);
  const availability = useAvailability(row?.item_id);
  const moves = useMoves(
    {
      location_id: row ? [row.location_id] : undefined,
      item_id: row?.item_id,
      lot_id: row?.lot_id ?? undefined,
      page: 1,
      page_size: HISTORY,
    },
    !!row,
  );

  if (!row) {
    // The row is not on this page any more -- a filter changed under an open
    // panel. Closing it silently would move the reader; saying so does not.
    return (
      <RecordPanel title="Existencias" open onClose={onClose}>
        <p className="text-14 text-ink-body">
          Esta fila ya no está en la página. Ábrala de nuevo desde la tabla.
        </p>
      </RecordPanel>
    );
  }

  const meaning = stateBadge(row);
  const clause = stockoutClause(row);
  const elsewhere = (availability.data?.by_location ?? []).filter(
    (one) => one.location_id !== row.location_id,
  );
  //  §1 deliverable 11 · units dispatched to **this** sede and not yet received.
  //  On no shelf, so never in `Existencias` and never a state in the `Estado`
  //  column -- a separate figure, and one whose absence is silence rather than
  //  a zero (§B.9.2 tier 3).
  const inTransit =
    availability.data?.by_location.find(
      (one) => one.location_id === row.location_id,
    )?.in_transit ?? 0;

  return (
    <>
      <RecordPanel
        title={row.item_name}
        open
        onClose={onClose}
        footer={
          elevated || me.role === "cashier" ? (
            <div className="flex items-center justify-between gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setMovement(true)}
              >
                Registrar movimiento
              </Button>
              {row.lot_id ? (
                // §1 deliverable 6 · **the trace is a link, not a button**, so
                // the address bar carries it: an INVIMA answer gets forwarded
                // to somebody else in the same building. The param stays on
                // whichever route opened it (`lot-trace.tsx`).
                <Link
                  from="/inventory"
                  search={(current) => ({ ...current, lote: row.lot_id! })}
                  className="rounded-control px-2 py-1 text-12 text-ink-body transition-colors duration-140 ease-out hover:text-ink"
                >
                  Ver trazabilidad del lote
                </Link>
              ) : null}
            </div>
          ) : undefined
        }
      >
        <Section title="Estado">
          <div className="flex flex-wrap items-center gap-2">
            <Badge family={meaning.family} dot={meaning.dot}>
              {meaning.label}
              {clause ? <span className="text-ink-body">{clause}</span> : null}
            </Badge>
            <StatusLine
              family={INVIMA_STATUS[row.invima_status]?.family ?? "neutral"}
              dot={INVIMA_STATUS[row.invima_status]?.dot ?? "solid"}
              label={
                INVIMA_STATUS[row.invima_status]?.label ?? row.invima_status
              }
            />
          </div>
          <Row label="Sede" value={row.location_name} />
          <Row
            label="Existencias"
            value={
              row.bar_percentage === null ? (
                <span className="tabular-nums">{count(row.quantity)}</span>
              ) : (
                <StockBar
                  fill={row.bar_percentage}
                  figure={count(row.quantity)}
                />
              )
            }
          />
          {row.state === "overstock" ? (
            // **`Sobrestock` carries no day figure before S6**, and the panel
            // is where that is explained rather than guessed. §B.9.2 tier 3: a
            // fabricated cover figure is worse than none, and a zero would be
            // the most expensive kind of lie this screen can tell.
            <p className="mt-2 text-12 text-ink-label">
              Está por encima del máximo definido. La cobertura en días llega
              con el pronóstico de demanda; todavía no existe, así que no la
              inventamos.
            </p>
          ) : null}
        </Section>

        <Section title="Umbrales">
          {row.policy_source === null ? (
            <p className="text-12 text-ink-label">
              Sin política definida. Esta referencia no puede llegar a «Punto de
              reorden» ni a «Sobrestock», y su barra no tiene contra qué
              medirse.
            </p>
          ) : (
            <p className="text-12 text-ink-label">
              {row.policy_source === "manual"
                ? "Fijados por una persona."
                : "Calculados por el modelo."}
            </p>
          )}
          <Thresholds row={row} editable={elevated} />
        </Section>

        {row.lot_id ? (
          <Section title="Lote">
            {lot.isPending ? (
              <SkeletonBar className={`${BAR.cell} w-40`} />
            ) : lot.isError ? (
              <RegionError
                title="No pudimos cargar el lote."
                detail="Vuelva a abrir el panel."
              />
            ) : lot.data ? (
              <>
                <Row label="Código" value={lot.data.lot_code} />
                <Row
                  label="Vence"
                  value={
                    lot.data.expires_at ? monthYear(lot.data.expires_at) : "—"
                  }
                />
                <Row
                  label="Proveedor"
                  value={lot.data.supplier_name ?? "Sin proveedor"}
                />
                <Row
                  label="Costo unitario"
                  value={
                    lot.data.unit_cost
                      ? money(Number(lot.data.unit_cost))
                      : "Sin costo registrado"
                  }
                />
                <Row
                  label="Registro sanitario"
                  value={lot.data.invima_registration || "El del producto"}
                />
              </>
            ) : null}
          </Section>
        ) : null}

        {inTransit > 0 ? (
          <Section title="En tránsito">
            <Row
              label="Hacia esta sede"
              value={<span className="tabular-nums">{count(inTransit)}</span>}
            />
            <p className="text-11 text-ink-label">
              Despachadas y todavía sin recibir. No están en ninguna estantería.
            </p>
          </Section>
        ) : null}

        <Section title="En otras sedes">
          {availability.isPending ? (
            <SkeletonBar className={`${BAR.cell} w-40`} />
          ) : elsewhere.length === 0 ? (
            <p className="text-12 text-ink-label">
              Ninguna otra sede tiene esta referencia.
            </p>
          ) : (
            elsewhere.map((one) => (
              <Row
                key={one.location_id}
                label={one.location_name}
                value={
                  <span className="tabular-nums">
                    {count(one.quantity)}
                    {one.in_transit > 0 ? (
                      <span className="ml-2 text-11 text-ink-label">
                        +{count(one.in_transit)} en tránsito
                      </span>
                    ) : null}
                  </span>
                }
              />
            ))
          )}
        </Section>

        <Section title={`Últimos ${HISTORY} movimientos`}>
          {moves.isPending ? (
            <SkeletonBar className={`${BAR.cell} w-full`} />
          ) : (moves.data?.rows.length ?? 0) === 0 ? (
            <p className="text-12 text-ink-label">
              Esta fila no tiene movimientos registrados.
            </p>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {moves.data!.rows.map((one) => (
                <li key={one.id} className="flex items-baseline gap-2">
                  <span className="w-14 shrink-0 text-right text-14 tabular-nums text-ink">
                    {one.quantity > 0 ? `+${one.quantity}` : one.quantity}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-12 text-ink-body">
                      {MOVE_TYPE[one.type] ?? one.type}
                      {one.reason ? ` · ${MOVE_REASON[one.reason]}` : ""}
                    </span>
                    <span className="block truncate text-11 text-ink-label">
                      {since(one.recorded_at)}
                      {one.user_name ? ` · ${one.user_name}` : ""}
                      {one.device_label ? ` · ${one.device_label}` : ""}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </RecordPanel>

      <MovementDialog
        open={movement}
        row={row}
        me={me}
        onClose={() => setMovement(false)}
      />
      <span hidden data-row-id={rowId} />
    </>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-hairline pb-4 pt-4 first:pt-0 last:border-b-0">
      <h3 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
        {title}
      </h3>
      <div className="mt-3 flex flex-col gap-1.5">{children}</div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="shrink-0 text-12 text-ink-label">{label}</span>
      <span className="min-w-0 truncate text-right text-14 text-ink">
        {value}
      </span>
    </div>
  );
}

/**
 * The thresholds, editable in place at `source = manual`.
 *
 * **A `cashier` sees the numbers and no editor** (§B.8.3): the fields are not
 * rendered rather than rendered disabled, and `PUT /api/stock-policies` refuses
 * the call if one is made anyway.
 */
function Thresholds({ row, editable }: { row: StockRow; editable: boolean }) {
  const save = useSavePolicies();
  const toast = useToast();
  const [values, setValues] = useState({
    min_quantity: row.min_quantity?.toString() ?? "",
    max_quantity: row.max_quantity?.toString() ?? "",
    reorder_point: row.reorder_point?.toString() ?? "",
    target_coverage_days: row.target_coverage_days?.toString() ?? "",
  });

  if (!editable) {
    return (
      <>
        <Row label="Punto de reorden" value={row.reorder_point ?? "—"} />
        <Row label="Máximo" value={row.max_quantity ?? "—"} />
        <Row label="Mínimo" value={row.min_quantity ?? "—"} />
      </>
    );
  }

  const number = (raw: string) => (raw.trim() === "" ? null : Number(raw));

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(
          {
            policies: [
              {
                item_id: row.item_id,
                location_id: row.location_id,
                min_quantity: number(values.min_quantity),
                max_quantity: number(values.max_quantity),
                reorder_point: number(values.reorder_point),
                target_coverage_days: number(values.target_coverage_days),
              },
            ],
          },
          {
            onSuccess: () =>
              toast(
                "Umbrales guardados. Quedan como «fijados por una persona».",
              ),
            onError: (error) => toast((error as Error).message),
          },
        );
      }}
    >
      <div className="grid grid-cols-2 gap-3">
        <Field label="Punto de reorden">
          <Input
            inputMode="numeric"
            value={values.reorder_point}
            onChange={(event) =>
              setValues({ ...values, reorder_point: event.currentTarget.value })
            }
          />
        </Field>
        <Field label="Máximo">
          <Input
            inputMode="numeric"
            value={values.max_quantity}
            onChange={(event) =>
              setValues({ ...values, max_quantity: event.currentTarget.value })
            }
          />
        </Field>
        <Field label="Mínimo">
          <Input
            inputMode="numeric"
            value={values.min_quantity}
            onChange={(event) =>
              setValues({ ...values, min_quantity: event.currentTarget.value })
            }
          />
        </Field>
        <Field label="Cobertura objetivo (días)">
          <Input
            inputMode="numeric"
            value={values.target_coverage_days}
            onChange={(event) =>
              setValues({
                ...values,
                target_coverage_days: event.currentTarget.value,
              })
            }
          />
        </Field>
      </div>
      <Button
        type="submit"
        size="sm"
        variant="secondary"
        busy={save.isPending}
        className="self-start"
      >
        Guardar umbrales
      </Button>
    </form>
  );
}
