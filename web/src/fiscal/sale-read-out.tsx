import { useSaleFiscalDocument } from "@/api/fiscal";
import { DOT } from "@/ui/format";

/**
 * **The sale's fiscal read-out**, rendered inside S4's sale detail.
 *
 * When the target returned identifiers, one line. When it returned nothing, or
 * when the handoff has not landed, or when no target is configured, **the region
 * renders nothing at all** — not a placeholder, not a status, and never a
 * skeleton that will not resolve (§B.9.2 tier 3, §8).
 *
 * **It carries no badge in any state.** The four handoff states are the work
 * list's, and putting one on a sale row would be a second surface rendering a
 * status this stage owns — which is exactly what the bundle grep in S5's
 * *Verification* exists to catch.
 *
 * **And it claims nothing about the DIAN.** The line says the client's own
 * invoicing system issued a document and gives its number. Botica never learns
 * what the DIAN did with it (§8, A9).
 */
export function SaleFiscalReadOut({ saleId }: { saleId: string | null }) {
  const query = useSaleFiscalDocument(saleId);
  const data = query.data;
  // Nothing while it loads, nothing on an error, nothing unconfigured, and
  // nothing where the target returned no identifier. Four ways to render
  // nothing and one to render a line, which is the right ratio for a region
  // that most instances never fill.
  if (!data?.configured) return null;
  if (!data.external_number && !data.pdf_url) return null;

  return (
    <p className="text-12 text-ink-label">
      {data.external_number
        ? `Factura del sistema de facturación ${DOT} ${data.external_number}`
        : "Factura del sistema de facturación"}
      {data.pdf_url ? (
        <>
          {" "}
          <a
            className="text-brand underline-offset-2 hover:underline"
            href={data.pdf_url}
            target="_blank"
            rel="noreferrer"
          >
            Ver documento
          </a>
        </>
      ) : null}
    </p>
  );
}
