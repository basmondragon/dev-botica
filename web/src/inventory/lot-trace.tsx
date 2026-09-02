import { Download } from "lucide-react";
import { useLot, useTrace } from "@/api/inventory";
import type { Me } from "@/api/queries";
import { DOT, count, money, monthYear, stamp } from "@/ui/format";
import { Modal } from "@/ui/panel";
import { RegionError, SkeletonRows } from "@/ui/states";
import { DataTable, TableFrame, TableScroll } from "@/ui/table";
import { MOVE_REASON, MOVE_TYPE } from "./vocabulary";

/**
 * §1 deliverable 6, acceptance 10 · **the recall answer, as a screen.**
 *
 * Given a lot: every move on it in `recorded_at` order with its document, its
 * device, its user, both clocks and the running balance after it, plus the
 * reverse lookup from a lot code to every sede holding it. This is what an
 * INVIMA withdrawal is answered with, and it is a deliverable rather than an
 * emergent property of having the data -- the query exists either way; the
 * screen is what makes it answerable by the person the inspector is standing
 * in front of.
 *
 * **The reverse lookup lives in the search field**, not in a second dialog:
 * Existencias searches `lot__lot_code` (`_stock_queryset`), so a code off a
 * withdrawal notice narrows the table to the sedes holding it and the row's
 * panel opens this. One way in, and it is the one somebody is already using.
 *
 * **It is a modal and not a route.** The nav stays at seven items and
 * `Inventario` at four routes (§B.8.1); a trace is opened from a lot somebody
 * is already looking at, or from a code somebody was handed over the phone, and
 * both of those are places rather than destinations. The lot id is a search
 * param on whichever route opened it, so the trace is still a link.
 *
 * **The balance is the check.** Its final value equals `SUM(quantity)` over the
 * projection for this lot across every sede; a trace whose last line disagrees
 * with the shelf is one nobody can hand to an inspector, so the two are shown
 * together and the disagreement is legible rather than hidden.
 */
export function LotTrace({
  lotId,
  me,
  onClose,
}: {
  lotId: string;
  me: Me;
  onClose: () => void;
}) {
  const elevated = me.role !== "cashier";
  // A `cashier` gets the lot and its sedes -- §2 grants them a network-wide
  // stock lookup -- and not the move-by-move trace, which is `owner`/`admin`
  // on the server. Asking for it anyway would render a 403 as a broken screen.
  const trace = useTrace(elevated ? lotId : null);
  const lot = useLot(elevated ? null : lotId);
  const row = trace.data?.lot ?? lot.data;
  const moves = trace.data?.moves ?? [];
  const balance = moves.length ? moves[moves.length - 1]!.balance : 0;
  const shelf = row?.total ?? 0;

  return (
    <Modal
      open
      size="wide"
      title={row ? `Trazabilidad del lote ${row.lot_code}` : "Trazabilidad"}
      onClose={onClose}
      footer={
        elevated && row ? (
          <a
            href={`/api/lots/${row.id}/trace.csv`}
            className="inline-flex h-8 items-center gap-1.5 rounded-control border border-edge px-3 text-12 text-ink-body transition-colors duration-140 ease-out hover:bg-hover-row hover:text-ink"
          >
            <Download aria-hidden strokeWidth={1.75} className="size-3.5" />
            Descargar CSV
          </a>
        ) : undefined
      }
    >
      {(elevated ? trace.isError : lot.isError) ? (
        <RegionError
          title="No pudimos cargar la trazabilidad de este lote."
          detail={
            ((elevated ? trace.error : lot.error) as Error | null)?.message ??
            ""
          }
        />
      ) : !row ? (
        <SkeletonRows rows={6} columns={SKELETON} />
      ) : (
        <div className="flex flex-col gap-5">
          <section className="grid grid-cols-2 gap-x-6 gap-y-2">
            <Fact label="Producto" value={row.item_name} />
            <Fact label="Lote" value={row.lot_code} />
            <Fact
              label="Vence"
              value={row.expires_at ? monthYear(row.expires_at) : "—"}
            />
            <Fact label="Proveedor" value={row.supplier_name ?? "—"} />
            <Fact
              label="Costo unitario"
              value={row.unit_cost ? money(Number(row.unit_cost)) : "—"}
            />
            <Fact
              label="Registro sanitario"
              value={row.invima_registration || "—"}
            />
          </section>

          <section>
            <Eyebrow>Dónde está</Eyebrow>
            {row.by_location.length === 0 ? (
              <p className="mt-2 text-12 text-ink-label">
                Ninguna sede tiene unidades de este lote.
              </p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1.5">
                {row.by_location.map((one) => (
                  <li
                    key={one.location_id}
                    className="flex items-baseline justify-between gap-4"
                  >
                    <span className="text-12 text-ink-label">
                      {one.location_name}
                    </span>
                    <span className="text-14 tabular-nums text-ink">
                      {count(one.quantity)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-11 text-ink-label">
              Total en estantería {count(shelf)}
              {elevated ? (
                <>
                  {" "}
                  {DOT} saldo al final del recorrido {count(balance)}
                  {balance !== shelf ? (
                    <span className="text-ink-critical">
                      {" "}
                      {DOT} el recorrido y la existencia no coinciden
                    </span>
                  ) : null}
                </>
              ) : null}
            </p>
          </section>

          {elevated ? (
            <section>
              <Eyebrow>Recorrido</Eyebrow>
              {trace.isPending ? (
                <SkeletonRows rows={5} columns={SKELETON} />
              ) : moves.length === 0 ? (
                <p className="mt-2 text-12 text-ink-label">
                  Este lote no tiene movimientos registrados.
                </p>
              ) : (
                <TableFrame className="mt-2 max-h-[46vh]">
                  <TableScroll>
                    <DataTable
                      rows={moves}
                      rowId={(one) => one.id}
                      density="compact"
                      columns={COLUMNS}
                    />
                  </TableScroll>
                </TableFrame>
              )}
            </section>
          ) : null}
        </div>
      )}
    </Modal>
  );
}

const SKELETON = ["17%", "22%", "13%", "30%", "9%", "9%"];

const COLUMNS = [
  {
    key: "recorded_at",
    label: "Registrado",
    width: "17%",
    truncate: true,
    render: (one: { recorded_at: string; occurred_at: string }) => (
      <span title={`En el equipo ${stamp(one.occurred_at)}`}>
        {stamp(one.recorded_at)}
      </span>
    ),
  },
  {
    key: "type",
    label: "Movimiento",
    width: "22%",
    truncate: true,
    render: (one: { type: string; reason: string }) =>
      `${MOVE_TYPE[one.type] ?? one.type}${
        one.reason ? ` · ${MOVE_REASON[one.reason] ?? one.reason}` : ""
      }`,
  },
  {
    key: "location_name",
    label: "Sede",
    width: "13%",
    truncate: true,
    render: (one: { location_name: string }) => one.location_name,
  },
  {
    key: "who",
    label: "Quién · equipo",
    // The widest column on the surface. **The device is half of the answer an
    // inspection asks for** -- `Hernán Salced…` with the equipo truncated away
    // is a trace that names nobody's till.
    width: "30%",
    truncate: true,
    render: (one: { user_name: string; device_label: string | null }) =>
      [one.user_name || "—", one.device_label].filter(Boolean).join(" · "),
  },
  {
    key: "quantity",
    label: "Cantidad",
    width: "9%",
    align: "right" as const,
    numeric: true,
    render: (one: { quantity: number }) =>
      one.quantity > 0 ? `+${count(one.quantity)}` : count(one.quantity),
  },
  {
    key: "balance",
    label: "Saldo",
    width: "9%",
    align: "right" as const,
    numeric: true,
    render: (one: { balance: number }) => count(one.balance),
  },
];

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <Eyebrow>{label}</Eyebrow>
      <p className="mt-1 truncate text-14 text-ink">{value}</p>
    </div>
  );
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
      {children}
    </h3>
  );
}
