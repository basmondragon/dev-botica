import { createFileRoute } from "@tanstack/react-router";
import { useMe, type Role } from "@/api/queries";
import { FiscalWorkList, type FiscalSearch } from "@/fiscal/work-list";
import { SEGMENTS, WORK_LIST, type Segment } from "@/fiscal/vocabulary";
import { Content, ShellSkeleton, TopBar } from "@/shell/shell";
import { roleLabel, roleList } from "@/shell/nav";
import { RouteError } from "@/ui/states";
import { PAGE_SIZES } from "@/ui/table";

const OFFICE: Role[] = ["owner", "admin", "platform_admin"];

const text = (value: unknown) =>
  typeof value === "string" && value ? value : undefined;

function validateSearch(search: Record<string, unknown>): FiscalSearch {
  const size = Number(search.pageSize);
  const page = Math.max(1, Number(search.page) || 1);
  const segment = SEGMENTS.find((one) => one.value === search.lista);
  return {
    page: page === 1 ? undefined : page,
    pageSize: PAGE_SIZES.includes(size as (typeof PAGE_SIZES)[number])
      ? size
      : undefined,
    lista: segment?.value as Segment | undefined,
    sede: text(search.sede),
    envio: text(search.envio),
    settings: text(search.settings),
  };
}

/**
 * `Envíos a facturación` — **an office route with no nav item.** §B.8.1 caps
 * the flat sidebar at seven and Botica is at it, so this is reached from the
 * Panel's strip (S9), from `Ver envíos` in the settings section, and from the
 * link in the daily digest.
 *
 * **The route resolves whether or not a target is configured**, because a route
 * that refused would be indistinguishable from a broken link — and the digest
 * email's link, the settings action and a saved bookmark all have to land
 * somewhere that explains itself.
 */
export const Route = createFileRoute("/_app/fiscal-documents")({
  validateSearch,
  component: Envios,
});

function Envios() {
  const me = useMe();
  const search = Route.useSearch();
  if (!me.data) return <ShellSkeleton />;
  // §B.8.3 · a `cashier` is offered no entry to this route, and a direct link
  // **refuses inside the content region** naming the role it needs. A fiscal
  // state is an administrator's work list and never a cashier's (§8).
  if (!OFFICE.includes(me.data.role)) {
    return (
      <>
        <TopBar breadcrumb={[]} title={WORK_LIST} />
        <Content>
          <RouteError
            title={`${WORK_LIST} requiere el perfil ${roleList(OFFICE)}.`}
            detail={
              `Su sesión es de perfil ${roleLabel(me.data.role)}. El envío al ` +
              "sistema de facturación no cambia nada del mostrador: las ventas " +
              "se registran y se cobran igual."
            }
          />
        </Content>
      </>
    );
  }
  return <FiscalWorkList me={me.data} search={search} />;
}
