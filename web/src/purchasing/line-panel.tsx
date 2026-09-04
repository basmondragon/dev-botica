import {
  useDemandForecast,
  type PurchaseOrderDetail,
  type PurchaseOrderLineRow,
} from "@/api/purchasing";
import { count as formatCount, money, percent, DOT } from "@/ui/format";
import { RecordPanel } from "@/ui/panel";
import { StatusDot } from "@/ui/status";
import {
  BAND_FAMILY,
  BAND_LABEL,
  BASIS_LABEL,
  coverageReading,
  reasonText,
} from "./vocabulary";

/**
 * §B.8.5 · the line's record panel: 440px, pushing rather than overlaying, so
 * the table behind it stays navigable -- which is the whole point of `j`/`k`.
 *
 * **Two readings, and both are shown.** `Al generar la orden` carries what was
 * stamped on the line the morning it was proposed; `Cómo lo calculó el modelo`
 * carries what the current forecast says it was computed from. A confidence
 * nobody can take apart is a number an administrator learns to ignore.
 */
export function LinePanel({
  line,
  order,
  onClose,
}: {
  line: PurchaseOrderLineRow | undefined;
  order: PurchaseOrderDetail["order"] | undefined;
  onClose: () => void;
}) {
  const forecast = useDemandForecast(order?.location_id, line?.item_id);
  if (!line) return null;
  const current = forecast.data?.rows?.[0];

  return (
    <RecordPanel title={line.item_name} open onClose={onClose}>
      <Block title="Producto">
        <Row label="Presentación" value={line.presentation || "—"} />
        <Row label="Laboratorio" value={line.manufacturer_name ?? "—"} />
      </Block>

      <Block title="Al generar la orden">
        <Row
          label="Base"
          value={line.basis ? BASIS_LABEL[line.basis] : "Escrita a mano"}
        />
        <Row
          label="Confianza"
          value={
            line.band ? (
              <span className="inline-flex items-center gap-[7px]">
                <StatusDot family={BAND_FAMILY[line.band]} dot="hollow" />
                {`${BAND_LABEL[line.band]} ${DOT} ${percent(
                  Number(line.confidence ?? 0) * 100,
                )}`}
              </span>
            ) : (
              "—"
            )
          }
        />
        <Row
          label="Cobertura"
          value={
            line.stamped_coverage_days === null ||
            line.stamped_coverage_days === undefined
              ? coverageReading(line)
              : `${formatCount(Math.round(Number(line.stamped_coverage_days)))} días`
          }
        />
        <Row label="Modelo" value={order?.source === "manual" ? "—" : "v1"} />
        <Row
          label="Sugerido"
          value={formatCount(line.suggested_quantity ?? 0)}
        />
        <Row label="Aprobado" value={formatCount(line.approved_quantity)} />
      </Block>

      <Block title="Cómo lo calculó el modelo">
        {current ? (
          <>
            <Row
              label="Semanas útiles"
              value={
                current.usable_weeks === null ||
                current.usable_weeks === undefined
                  ? "sin histórico"
                  : `${formatCount(current.usable_weeks)} después del censado`
              }
            />
            <Row
              label="Variación"
              value={
                current.variation === null || current.variation === undefined
                  ? "—"
                  : percent(Number(current.variation) * 100)
              }
            />
            <Row
              label="Histórico cargado"
              value={
                current.imported_share === null ||
                current.imported_share === undefined
                  ? "—"
                  : percent(Number(current.imported_share) * 100)
              }
            />
            <Row
              label="Punto de reorden"
              value={formatCount(current.reorder_point)}
            />
            <Row
              label="Existencia de seguridad"
              value={formatCount(current.safety_stock)}
            />
          </>
        ) : (
          <p className="text-12 text-ink-label">
            El modelo todavía no ha corrido para esta referencia en esta sede.
          </p>
        )}
      </Block>

      <Block title="Proveedor">
        <Row label="Proveedor" value={order?.supplier_name ?? "—"} />
        <Row
          label="Costo unitario"
          value={
            line.unit_cost === null || line.unit_cost === undefined
              ? "sin costo registrado"
              : money(Number(line.unit_cost))
          }
        />
        <Row label="Recibido" value={formatCount(line.received_quantity)} />
      </Block>

      <Block title="Por qué">
        <p className="text-14 text-ink-body">{reasonText(line)}</p>
        <p className="mt-1.5 font-mono text-10 uppercase tracking-eyebrow text-ink-note">
          {line.reason_code || "sin código"}
        </p>
      </Block>
    </RecordPanel>
  );
}

function Block({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6 last:mb-0">
      <h3 className="mb-2.5 font-mono text-10 uppercase tracking-eyebrow text-ink-note">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-hairline py-2 last:border-b-0">
      <span className="shrink-0 text-12 text-ink-label">{label}</span>
      <span className="min-w-0 text-right text-14 tabular-nums text-ink">
        {value}
      </span>
    </div>
  );
}
