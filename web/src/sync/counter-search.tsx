import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { Button } from "@/ui/button";
import { Input } from "@/ui/field";
import { DOT, count, money } from "@/ui/format";
import { Modal } from "@/ui/panel";
import { EmptyState } from "@/ui/states";
import { useSync } from "./context";
import { searchCatalog, type CatalogHit } from "./local";

/**
 * **Buscar en el catálogo** — the honest proof that the catalog is in the
 * browser.
 *
 * A stage whose only demonstrable output is a settings table proves nothing
 * about the substrate. This box is the check criterion 3 measures: keystroke to
 * filtered list over 4.284 items **in under 30 ms p95, with no network in the
 * path** (§4). It reads the local store and nothing else, and it works with the
 * plug out.
 *
 * §B.9.2 tier 1 · this sede's catalog and this sede's price, read from the local
 * store, render at **full strength with no staleness marker**. Adding a marker
 * to the numbers that are right is how a marker stops meaning anything; the
 * price list's own freshness is stated once, in the sync panel.
 */
export function CatalogSearch({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const sync = useSync();
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<CatalogHit[]>([]);
  const [searched, setSearched] = useState(false);
  const field = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open || !sync.database) return;
    // **No debounce.** §4's budget is keystroke to filtered list, and a
    // debounce would hide exactly the latency criterion 3 measures. A local
    // indexed query over four thousand items does not need one.
    let stale = false;
    void searchCatalog(sync.database, term).then((rows) => {
      if (stale) return;
      setHits(rows);
      setSearched(term.trim().length > 0);
    });
    return () => {
      stale = true;
    };
  }, [open, term, sync.database]);

  useEffect(() => {
    if (open) field.current?.focus();
  }, [open]);

  return (
    <Modal open={open} title="Buscar en el catálogo" onClose={onClose}>
      <div className="flex flex-col gap-4">
        <div className="relative">
          <Search
            aria-hidden
            strokeWidth={1.5}
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-soft"
          />
          <Input
            ref={field}
            value={term}
            placeholder="Nombre, principio activo o código"
            aria-label="Buscar en el catálogo"
            className="pl-9"
            onChange={(event) => setTerm(event.target.value)}
          />
        </div>

        {hits.length === 0 ? (
          <EmptyState
            kind={searched ? "filtered" : "deliberate"}
            title={
              searched
                ? "Ningún producto coincide con esta búsqueda"
                : "Escriba para buscar"
            }
            body={
              searched
                ? `Búsqueda activa: «${term}». El catálogo de ${sync.device?.location_name ?? "esta sede"} está completo en este equipo.`
                : "El catálogo está en este equipo y responde sin conexión."
            }
            actionLabel={searched ? "Quitar filtros" : undefined}
            onAction={searched ? () => setTerm("") : undefined}
          />
        ) : (
          <ul className="flex max-h-[420px] flex-col overflow-y-auto">
            {hits.map((hit) => (
              <li
                key={hit.id}
                className="flex items-baseline justify-between gap-4 border-b border-hairline py-2.5 last:border-b-0"
              >
                <span className="min-w-0">
                  <span className="block truncate text-14 text-ink">
                    {hit.name}
                  </span>
                  {hit.presentation ? (
                    <span className="block truncate text-11 text-ink-label">
                      {hit.presentation}
                    </span>
                  ) : null}
                </span>
                <span className="shrink-0 text-14 tabular-nums text-ink">
                  {/* §B.9.2 tier 3 · a price this sede has no row for renders
                      as `—` with its reason, never as a zero. A zero standing
                      in for "we don't know" is the single most expensive lie an
                      inventory system can tell. */}
                  {hit.price === null ? (
                    <span className="text-12 text-ink-soft">— sin precio</span>
                  ) : (
                    money(Number(hit.price))
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}

        <p className="text-11 text-ink-soft">
          {count(hits.length)} {hits.length === 1 ? "resultado" : "resultados"}{" "}
          {DOT} se lee de este equipo, sin conexión
        </p>

        <div className="flex justify-end">
          <Button variant="secondary" size="md" onClick={onClose}>
            Cerrar
          </Button>
        </div>
      </div>
    </Modal>
  );
}
