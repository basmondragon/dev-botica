import { createFileRoute } from "@tanstack/react-router";
import type {
  ConfidenceBand,
  ProposalBasis,
  RowStateFilter,
} from "@/api/pricing";
import { useMe } from "@/api/queries";
import { PricingPage, type PricingSearch } from "@/pricing/pricing-page";
import { NAV } from "@/shell/nav";
import { ShellSkeleton } from "@/shell/shell";
import { RoleGate } from "@/shell/stage-route";
import { PAGE_SIZES } from "@/ui/table";

const ITEM = NAV.find((item) => item.key === "pricing")!;

const STATES = new Set<RowStateFilter>([
  "live",
  "unevaluated",
  "no_proposal",
  "proposed",
  "above_cap",
  "taken",
  "modified",
  "dismissed",
  "superseded",
]);

const text = (value: unknown) =>
  typeof value === "string" && value ? value : undefined;

/**
 * §9 + §B.4 · page, size, sort and every filter live in the URL, so any view is
 * a link and pasting it into another browser reproduces it. The open record
 * panel is a search param for the same reason.
 */
function validateSearch(search: Record<string, unknown>): PricingSearch {
  const size = Number(search.pageSize);
  const page = Math.max(1, Number(search.page) || 1);
  const state = text(search.estadoPrecio) as RowStateFilter | undefined;
  const basis = text(search.basePrecio) as ProposalBasis | undefined;
  const band = text(search.confianza) as ConfidenceBand | undefined;
  return {
    q: text(search.q),
    laboratorio: text(search.laboratorio),
    categoria: text(search.categoria),
    // `live` is the default view -- `Estado · Con propuesta` -- so a key at its
    // default is dropped and the URL carries only what differs from it.
    estadoPrecio:
      state && STATES.has(state) && state !== "live" ? state : undefined,
    basePrecio:
      basis === "margin_rule" || basis === "elasticity" ? basis : undefined,
    confianza:
      band === "high" || band === "medium" || band === "low" ? band : undefined,
    page: page === 1 ? undefined : page,
    pageSize: PAGE_SIZES.includes(size as (typeof PAGE_SIZES)[number])
      ? size
      : undefined,
    sort: text(search.sort),
    order: search.order === "desc" ? "desc" : undefined,
    ref: text(search.ref),
    adopcion: text(search.adopcion),
    settings: text(search.settings),
  };
}

export const Route = createFileRoute("/_app/pricing")({
  validateSearch,
  component: Precios,
});

function Precios() {
  const me = useMe();
  const search = Route.useSearch();
  if (!me.data) return <ShellSkeleton />;
  return (
    <RoleGate item={ITEM}>
      <PricingPage search={search} />
    </RoleGate>
  );
}
