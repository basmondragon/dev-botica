import { usePricingItem, type PricingRow } from "@/api/pricing";
import { Button } from "@/ui/button";
import { count, dayMonth, DOT, money, percent, points } from "@/ui/format";
import { RecordPanel } from "@/ui/panel";
import { RegionError, SkeletonBar } from "@/ui/states";
import { Badge } from "@/ui/status";
import { BASIS_LABEL, CONFIDENCE_LABEL, ROW_STATE } from "./vocabulary";

/**
 * §B.8.5 · the 440px record panel, pushing rather than overlaying, so the table
 * behind it stays navigable.
 *
 * **It opens with the basis in one line**, because that is the sentence that
 * decides how much weight everything below it carries. Then the suggestion's
 * arithmetic line by line, then the estimate, then the cap and its source, then
 * the price history with each row's author, then **every past suggestion beside
 * what the person chose against it** -- the measurement in its single-reference
 * form, which is where an owner arguing with the model gets to check whether the
 * model has been right before.
 */
export function PricingRecordPanel({
  itemId,
  goal,
  onClose,
  onAdjust,
}: {
  itemId: string;
  goal: number | null;
  onClose: () => void;
  onAdjust: (row: PricingRow) => void;
}) {
  const detail = usePricingItem(itemId);
  const row = detail.data?.row;
  const proposal = detail.data?.proposal;
  const estimate = detail.data?.estimate;

  return (
    <RecordPanel
      title={row?.name ?? "Referencia"}
      open
      onClose={onClose}
      footer={
        row && proposal?.status === "proposed" ? (
          <div className="flex justify-end">
            <Button variant="secondary" onClick={() => onAdjust(row)}>
              Ajustar precio
            </Button>
          </div>
        ) : undefined
      }
    >
      {detail.isPending ? (
        <div className="flex flex-col gap-3">
          <SkeletonBar className="h-3.5 w-3/4" />
          <SkeletonBar className="h-3.5 w-full" />
          <SkeletonBar className="h-3.5 w-2/3" />
        </div>
      ) : detail.isError || !row ? (
        <RegionError
          title="No pudimos cargar esta referencia."
          detail="Vuelva a intentarlo."
          onRetry={() => void detail.refetch()}
        />
      ) : (
        <div className="flex flex-col gap-6">
          <header className="flex flex-col gap-2">
            <p className="text-12 text-ink-label">
              {[row.presentation, row.manufacturer_name]
                .filter(Boolean)
                .join(` ${DOT} `)}
            </p>
            <Badge
              family={ROW_STATE[row.state].family}
              dot={ROW_STATE[row.state].dot}
            >
              {ROW_STATE[row.state].label}
            </Badge>
            {/* The one line that decides how much weight everything below
                carries. */}
            <p className="text-14 text-ink">
              {proposal
                ? proposal.basis === "elasticity"
                  ? `Propuesta por elasticidad ${DOT} confianza ${CONFIDENCE_LABEL[proposal.confidence].toLowerCase()}`
                  : "Propuesta por meta de margen · sin elasticidad estimada"
                : "Sin propuesta esta semana"}
            </p>
            {row.reason ? (
              <p className="text-12 text-ink-body">{row.reason}</p>
            ) : null}
            {proposal?.stale ? (
              <p className="text-12 text-ink-note">{proposal.stale.detail}</p>
            ) : null}
          </header>

          <Section title="La aritmética">
            <Line
              label="Precio actual"
              value={money(Number(row.current_price ?? 0))}
            />
            <Line
              label="Costo base"
              value={
                row.cost_basis === null
                  ? "Sin costo cargado"
                  : `${money(Number(row.cost_basis))} ${DOT} ${
                      row.cost_source === "lots"
                        ? "lotes en existencia"
                        : "lista del proveedor"
                    }`
              }
            />
            <Line
              label="Margen neto de IVA"
              value={
                row.current_margin === null
                  ? "—"
                  : percent(Number(row.current_margin))
              }
            />
            {proposal ? (
              <>
                <Line
                  label="Precio sugerido"
                  value={`${money(Number(proposal.suggested_price))} (${
                    Number(proposal.step_pct) >= 0 ? "+" : ""
                  }${percent(Number(proposal.step_pct))})`}
                />
                <Line
                  label="Margen proyectado"
                  value={
                    proposal.projected_margin === null
                      ? "—"
                      : percent(Number(proposal.projected_margin))
                  }
                />
                {proposal.margin_gap_pp !== null && goal !== null ? (
                  <Line
                    label="Falta para la meta"
                    value={`${points(Number(proposal.margin_gap_pp)).replace("+", "")} ${DOT} meta ${percent(goal)}`}
                  />
                ) : null}
                <Line
                  label="Impacto mensual"
                  value={
                    proposal.estimated_monthly_impact === null
                      ? "Sin volumen en los últimos 30 días"
                      : `${money(Number(proposal.estimated_monthly_impact))} ${DOT} sobre ${count(proposal.trailing_monthly_units ?? 0)} unidades`
                  }
                />
                <p className="text-11 text-ink-note">
                  La proyección mantiene el costo constante: una propuesta es un
                  análisis de un precio, no un pronóstico de un costo.
                </p>
              </>
            ) : null}
          </Section>

          {/* **On a `margin_rule` row the estimate block is not empty and is
              not hidden**: it renders the `elasticity_status` sentence as the
              reason the margin rule owns this reference, which is a statement
              of method rather than an apology. And **the confidence band never
              appears without `observations` and `r2` beside it** -- a band on
              its own is how a model launders its uncertainty. */}
          <Section title="La estimación">
            {estimate ? (
              <>
                <p className="text-12 text-ink-body">{estimate.reason}</p>
                {estimate.elasticity !== null ? (
                  <>
                    <Line
                      label="β"
                      value={String(estimate.elasticity).replace(".", ",")}
                    />
                    <Line
                      label="r²"
                      value={String(estimate.r2 ?? "—").replace(".", ",")}
                    />
                    <Line
                      label="Semanas con venta"
                      value={count(estimate.observations)}
                    />
                    <Line
                      label="Precios distintos"
                      value={count(estimate.distinct_prices)}
                    />
                    <Line
                      label="Intervalo 90%"
                      value={
                        estimate.ci_low === null || estimate.ci_high === null
                          ? "—"
                          : `${String(estimate.ci_low).replace(".", ",")} … ${String(estimate.ci_high).replace(".", ",")}`
                      }
                    />
                    <Line
                      label="Confianza"
                      value={
                        CONFIDENCE_LABEL[
                          (estimate.confidence || "low") as
                            "high" | "medium" | "low"
                        ]
                      }
                    />
                  </>
                ) : null}
                {estimate.weeks_excluded_stockout ? (
                  <Line
                    label="Semanas excluidas"
                    value={`${count(estimate.weeks_excluded_stockout)} por quiebre${
                      estimate.weeks_excluded_promo
                        ? `, ${count(estimate.weeks_excluded_promo)} por descuento`
                        : ""
                    }`}
                  />
                ) : null}
                {estimate.imported_share !== null &&
                Number(estimate.imported_share) > 0 ? (
                  <Line
                    label="Histórico importado"
                    value={percent(Number(estimate.imported_share) * 100)}
                  />
                ) : null}
                <Line
                  label="Ventana"
                  value={`${estimate.window} ${DOT} ${estimate.model_version}`}
                />
              </>
            ) : (
              <p className="text-12 text-ink-note">
                Todavía no se ha calculado esta referencia.
              </p>
            )}
            {(detail.data?.location_estimates ?? []).length > 0 ? (
              <div className="mt-2 flex flex-col gap-1">
                <p className="text-11 text-ink-note">
                  Por sede, solo como lectura: una propuesta es de red y v1 no
                  puede expresar dos precios para una referencia.
                </p>
                {detail.data!.location_estimates.map((one) => (
                  <p
                    key={String(one.location_id)}
                    className="text-11 text-ink-body"
                  >
                    {`${one.location_name} ${DOT} β ${String(one.elasticity ?? "—").replace(".", ",")}`}
                  </p>
                ))}
              </div>
            ) : null}
          </Section>

          <Section title="Tope regulado">
            <Line
              label="Estado"
              value={
                row.cap_status === "capped"
                  ? `${money(Number(row.regulated_max_price ?? 0))}`
                  : row.cap_status === "not_regulated"
                    ? "Sin regulación de precio"
                    : "Desconocido"
              }
            />
            {detail.data?.cap_source ? (
              <Line label="Fuente" value={detail.data.cap_source} />
            ) : null}
            {row.cap_status === "unknown" ? (
              <p className="text-11 text-ink-note">
                Un tope desconocido no es un tope ausente. Las alzas están
                desactivadas para esta referencia hasta que se cargue uno.
              </p>
            ) : null}
          </Section>

          <Section title="Historial de precio">
            {(detail.data?.prices ?? []).map((one) => (
              <p key={String(one.id)} className="text-12 text-ink-body">
                <span className="tabular-nums text-ink">
                  {money(Number(one.price))}
                </span>
                <span className="text-11 text-ink-note">
                  {` ${DOT} ${
                    one.source === "imported"
                      ? "cargado del sistema anterior"
                      : one.set_by_name
                        ? `fijado por ${one.set_by_name}`
                        : "fijado a mano"
                  } ${DOT} ${dayMonth(one.effective_from)}${
                    one.proposal_id ? ` ${DOT} desde una propuesta` : ""
                  }`}
                </span>
              </p>
            ))}
          </Section>

          {/* **Every past suggestion beside what the person chose against it.**
              This is where an owner arguing with the model checks whether the
              model has been right before. */}
          <Section title="Propuestas anteriores">
            {(detail.data?.history ?? []).length === 0 ? (
              <p className="text-12 text-ink-note">
                Esta es la primera propuesta sobre esta referencia.
              </p>
            ) : (
              detail.data!.history.map((one) => (
                <p key={String(one.id)} className="text-12 text-ink-body">
                  <span className="tabular-nums text-ink">
                    {money(Number(one.suggested_price))}
                  </span>
                  <span className="text-11 text-ink-note">
                    {` ${DOT} ${BASIS_LABEL[one.basis]} ${DOT} ${dayMonth(one.computed_at)} ${DOT} ${
                      ROW_STATE[one.status as keyof typeof ROW_STATE]?.label ??
                      one.status
                    }${
                      one.resolved_price !== null
                        ? ` en ${money(Number(one.resolved_price))}`
                        : ""
                    }${one.resolved_by_name ? ` por ${one.resolved_by_name}` : ""}`}
                  </span>
                </p>
              ))
            )}
          </Section>
        </div>
      )}
    </RecordPanel>
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
    <section className="flex flex-col gap-1.5">
      <h3 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-12">
      <span className="text-ink-label">{label}</span>
      <span className="text-right tabular-nums text-ink">{value}</span>
    </div>
  );
}
