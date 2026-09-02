import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { NAV, canReach, roleLabel, roleList } from "@/shell/nav";
import { Content, TopBar } from "@/shell/shell";
import { useMe } from "@/api/queries";
import { useSync } from "@/sync/context";
import { CatalogSearch } from "@/sync/counter-search";
import { RegisterCustomer } from "@/sync/register-customer";
import { SyncPanel } from "@/sync/sync-panel";
import { SyncStatus } from "@/sync/sync-status";
import { Button } from "@/ui/button";
import { EmptyState, RouteError } from "@/ui/states";

const ITEM = NAV.find((item) => item.key === "counter")!;

/**
 * Mostrador · `Venta`. **S4 fills this route; at S2 it is deliberately not
 * filler.**
 *
 * A stage whose only demonstrable output is a settings table proves nothing
 * about the substrate. What renders here is the header with `SyncStatus` in its
 * right slot, a never-populated empty state that names what will fill it, and
 * `Buscar en el catálogo` — the honest proof that 4.284 items are in this
 * browser and answer in under 30 ms with the network unplugged.
 */
function Counter() {
  const me = useMe();
  const sync = useSync();
  const [searching, setSearching] = useState(false);
  const [registering, setRegistering] = useState(false);

  // §B.13.3 · `F8` opens the sync panel. **No single-letter shortcut on any
  // till surface**: a scan is a burst of characters followed by `Enter`, and
  // any surface where `j` means something is a surface where scanning a product
  // code navigates.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "F8") {
        event.preventDefault();
        sync.setPanelOpen(!sync.panelOpen);
      }
      if (event.key === "F4") {
        event.preventDefault();
        setSearching(true);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [sync]);

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

  return (
    <>
      <TopBar
        breadcrumb={["Mostrador"]}
        title="Venta"
        actions={
          <div className="relative">
            <SyncStatus placement="counter" />
            <SyncPanel className="absolute right-0 top-12" />
          </div>
        }
      />
      <Content>
        {/* A4 · this browser replicates only for a till. An office identity
            opening Mostrador is told the truth about what it is looking at
            rather than shown a catalog claim that is false for their session. */}
        {sync.snapshot ? (
          <>
            <EmptyState
              title="Este equipo está listo para vender"
              body={`El mostrador se habilita en la próxima entrega. El catálogo de ${
                sync.device?.location_name ?? "esta sede"
              } ya está en este equipo y funciona sin conexión.`}
              actionLabel="Buscar en el catálogo"
              onAction={() => setSearching(true)}
            />
            {/* The one client-originated write that exists this early. It is a
                secondary beside the primary above because the counter is not a
                place to register customers for their own sake — it is where a
                sale needs one named, and offline (ledger rule 8). */}
            <div className="mt-4 flex justify-center">
              <Button
                variant="secondary"
                size="md"
                onClick={() => setRegistering(true)}
              >
                Registrar cliente
              </Button>
            </div>
          </>
        ) : (
          <EmptyState
            kind="deliberate"
            title="El mostrador se atiende desde una caja registrada"
            body="Este navegador no descarga el catálogo: una sesión de oficina lee el servidor, no una copia local. Registre este equipo en Ajustes · Sedes y dispositivos y entre con una cuenta de mostrador."
          />
        )}
        <CatalogSearch open={searching} onClose={() => setSearching(false)} />
        <RegisterCustomer
          open={registering}
          onClose={() => setRegistering(false)}
        />
      </Content>
    </>
  );
}

export const Route = createFileRoute("/_app/counter")({ component: Counter });
