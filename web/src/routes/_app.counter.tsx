import { createFileRoute } from "@tanstack/react-router";
import { NAV, canReach, roleLabel, roleList } from "@/shell/nav";
import { Content, TopBar } from "@/shell/shell";
import { useMe } from "@/api/queries";
import { useSync } from "@/sync/context";
import { CounterOffice, type CounterSearch } from "@/counter/office";
import { Till } from "@/counter/till";
import { EmptyState } from "@/ui/states";
import { RouteError } from "@/ui/states";

const ITEM = NAV.find((item) => item.key === "counter")!;

/**
 * Mostrador · one route, two surfaces, chosen by role.
 *
 * **A `cashier` gets the till and an `owner` or `admin` gets the office's
 * list** (§B.8.1). The nav has seven items and that is the ceiling: an eighth
 * called `Ventas` would cost a cashier attention every day to save an
 * administrator a click a month. So the two read models share a route, which is
 * also the honest shape — they are two views of the same thing (§4).
 */
function Counter() {
  const me = useMe();
  const sync = useSync();
  const search = Route.useSearch();

  if (me.data && !canReach(ITEM, me.data.role)) {
    return (
      <>
        <TopBar breadcrumb={[]} title={ITEM.label} />
        <Content>
          <RouteError
            title={`${ITEM.label} requiere el perfil ${roleList(ITEM.roles)}.`}
            detail={`Su sesión es de perfil ${roleLabel(me.data.role)}. Pida acceso a la propietaria de su droguería.`}
          />
        </Content>
      </>
    );
  }

  if (!me.data) return null;

  if (me.data.role !== "cashier") {
    return <CounterOffice me={me.data} search={search} />;
  }

  // A4 · this browser replicates only for a till. A cashier on a browser nobody
  // registered is told the truth about what they are looking at rather than
  // shown a ticket they cannot ring.
  if (!sync.snapshot) {
    return (
      <>
        <TopBar breadcrumb={["Mostrador"]} title="Venta" />
        <Content>
          <EmptyState
            kind="deliberate"
            title="El mostrador se atiende desde una caja registrada"
            body="Este navegador no descarga el catálogo: una sesión de oficina lee el servidor, no una copia local. Registre este equipo en Ajustes · Sedes y dispositivos y entre con una cuenta de mostrador."
          />
        </Content>
      </>
    );
  }

  return <Till me={me.data} />;
}

export const Route = createFileRoute("/_app/counter")({
  component: Counter,
  validateSearch: (search: Record<string, unknown>): CounterSearch => ({
    segment: (search.segment as CounterSearch["segment"]) || undefined,
    page: search.page ? Number(search.page) : undefined,
    pageSize: search.pageSize ? Number(search.pageSize) : undefined,
    sedes: Array.isArray(search.sedes) ? (search.sedes as string[]) : undefined,
    estadoVenta:
      (search.estadoVenta as CounterSearch["estadoVenta"]) || undefined,
    estadoTurno:
      (search.estadoTurno as CounterSearch["estadoTurno"]) || undefined,
    q: (search.q as string) || undefined,
    venta: (search.venta as string) || undefined,
    turno: (search.turno as string) || undefined,
    devolucion: (search.devolucion as string) || undefined,
    settings: (search.settings as string) || undefined,
  }),
});
