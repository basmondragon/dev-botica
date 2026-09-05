import { usePricingAdoption } from "@/api/pricing";
import { Button } from "@/ui/button";
import { count, DOT, percent } from "@/ui/format";
import { Panel, SectionHeader } from "@/ui/panel";
import { RegionError, SkeletonBar } from "@/ui/states";
import { BASIS_LABEL } from "./vocabulary";

/**
 * **The measurement, and the reason A11 kept `proposal_id` and `resolved_price`
 * instead of simplifying them away.**
 *
 * An engine that can only suggest is measured by what people did with the
 * suggestions: `suggested_price` is what we said, `resolved_price` is what they
 * chose, `basis` is which engine said it, and `item_prices.proposal_id` is S1's
 * proof that the suggestion and the price change are the same event.
 *
 * **Split by `basis`, always**, because the two engines make two different
 * claims and a blended adoption rate hides the case this stage most needs to
 * detect: an elasticity engine nobody trusts, carried by a margin rule
 * everybody uses. **The denominator sits beside every share** -- a 100% take
 * rate on three suggestions is not a finding.
 *
 * **It is not impact.** It measures whether people agreed with the suggestions,
 * not whether the suggestions were right; a margin that rose and a customer who
 * quietly went elsewhere are both invisible here. Presenting adoption as though
 * it were impact is the specific error this panel's own footnote exists to
 * prevent.
 */
export function AdoptionPanel({ onClose }: { onClose: () => void }) {
  const adoption = usePricingAdoption();

  if (adoption.isError) {
    return (
      <RegionError
        title="No pudimos cargar la adopción."
        detail="Vuelva a intentarlo."
        onRetry={() => void adoption.refetch()}
      />
    );
  }

  return (
    <Panel>
      <SectionHeader
        title="Adopción de las propuestas"
        counter={
          adoption.data
            ? `${adoption.data.since} → ${adoption.data.until}`
            : undefined
        }
        action={
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cerrar
          </Button>
        }
      />
      <div className="grid grid-cols-2 gap-px bg-hairline">
        {adoption.isPending
          ? [0, 1].map((one) => (
              <div key={one} className="bg-surface p-5">
                <SkeletonBar className="h-3 w-24" />
                <SkeletonBar className="mt-4 h-3.5 w-full" />
                <SkeletonBar className="mt-2 h-3.5 w-4/5" />
              </div>
            ))
          : (adoption.data?.by_basis ?? []).map((one) => (
              <div key={one.basis} className="bg-surface p-5">
                <p className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
                  {BASIS_LABEL[one.basis]}
                </p>
                <dl className="mt-3 flex flex-col gap-2 text-12">
                  <Row
                    label="Tomadas sin cambio"
                    value={one.taken}
                    total={one.resolved}
                  />
                  <Row
                    label="Ajustadas"
                    value={one.modified}
                    total={one.resolved}
                    note={
                      one.median_signed_gap_pct === null
                        ? undefined
                        : `mediana ${percent(Number(one.median_signed_gap_pct))} frente a lo sugerido`
                    }
                  />
                  <Row
                    label="Descartadas"
                    value={one.dismissed}
                    total={one.resolved}
                    note={
                      one.dismissed === 0
                        ? undefined
                        : `${count(one.dismissed_then_repriced)} se repreciaron a mano dentro del mes`
                    }
                  />
                  <Row
                    label="Reemplazadas sin mirar"
                    value={one.superseded}
                    total={one.proposed_ever}
                  />
                </dl>
                {one.resolved === 0 ? (
                  <p className="mt-3 text-11 text-ink-note">
                    Todavía nadie ha actuado sobre una propuesta de esta base.
                  </p>
                ) : null}
              </div>
            ))}
      </div>
      <p className="border-t border-hairline px-5 py-3 text-11 text-ink-note">
        Mide si las propuestas se están tomando, no si eran acertadas. El efecto
        sobre el margen se calcula en Reportes, sobre las referencias que
        cambiaron de precio.
      </p>
    </Panel>
  );
}

function Row({
  label,
  value,
  total,
  note,
}: {
  label: string;
  value: number;
  total: number;
  note?: string;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="flex-1 text-ink-body">{label}</dt>
      <dd className="shrink-0 text-right tabular-nums text-ink">
        {count(value)}
        {/* **The denominator beside every share**, always. */}
        <span className="text-ink-note">{` de ${count(total)}`}</span>
        {note ? (
          <span className="block text-11 text-ink-note">{`${DOT} ${note}`}</span>
        ) : null}
      </dd>
    </div>
  );
}
