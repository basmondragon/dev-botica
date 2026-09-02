import { useState } from "react";
import { ApiError } from "@/api/client";
import { useLocations, type Me } from "@/api/queries";
import {
  useClaimDevice,
  useDevices,
  useRevokeDevice,
  useSaveSyncSettings,
  useSyncConflicts,
  useSyncSettings,
  useUpdateDevice,
  type Device,
  type SyncSettings,
} from "@/api/sync";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { DOT, since } from "@/ui/format";
import { ConfirmDialog, Modal } from "@/ui/panel";
import { Select } from "@/ui/select";
import { Badge, type Meaning } from "@/ui/status";
import { DataTable, TableFooter } from "@/ui/table";
import { EmptyState, RegionError } from "@/ui/states";
import { useLocalGrid } from "@/ui/use-grid";
import { useToast } from "@/ui/toast";
import { FilterBar } from "@/ui/filter-bar";
import {
  recordFromClaim,
  requestPersistence,
  writeDevice,
} from "@/sync/device";
import { SectionHeading } from "./section";

/**
 * §B.8.4·4 · **Ajustes · Sedes y dispositivos**, the devices half.
 *
 * The `sync` settings group is a **block under the device list** rather than a
 * tenth rail item: §B.8.4·4 fixes nine sections and none of them is
 * `Sincronización`, and the decision taken here is to put the controls where an
 * administrator is already looking rather than to add a section for ten fields.
 * The cost if that is wrong is one search in the dialog's own search field; the
 * alternative costs every administrator a longer list forever.
 */

/**
 * The conflict vocabulary, in Spanish. A raw enum value on a screen is a raw
 * exception by another name (§B.10.3): `payload_rejected` tells an
 * administrator nothing they can act on.
 */
const CONFLICT_LABELS: Record<string, string> = {
  foreign_tenant: "Filas de otra droguería",
  foreign_location: "Filas de otra sede",
  unknown_collection: "Colección desconocida",
  payload_rejected: "El servidor rechazó los datos",
  device_revoked: "Equipo dado de baja",
  device_silent: "Equipo sin sincronizar",
  negative_stock: "Existencias en negativo",
  stale_price: "Precio desactualizado",
  catalog_divergence: "Catálogo divergente",
};

/**
 * §B.7.3 · `Estado` is the one badged column, and it is the column the surface
 * is about.
 *
 * `Almacenamiento sin proteger` is **hollow** because the browser has not yet
 * evicted anything and may never — §B.7.2's ring is for a system waiting on
 * something outside itself.
 */
function meaning(device: Device, staleHours: number): Meaning {
  if (device.status === "revoked")
    return { family: "neutral", dot: "solid", label: "Dado de baja" };
  // **The tenant's `stale_device_hours`, not a constant.** The daily job uses
  // that setting to decide which device is quiet, and the setting is edited in
  // the block directly below this table — a hard-coded 48 here would put the
  // badge and the conflict queue on two different thresholds the moment an
  // administrator moved it, on the one screen that shows both.
  const quiet = device.last_synced_at
    ? Date.now() - new Date(device.last_synced_at).getTime() >
      staleHours * 3600_000
    : true;
  if (quiet) {
    return {
      family: "warning",
      dot: "solid",
      label: device.last_synced_at
        ? `Sin sincronizar ${since(device.last_synced_at)}`
        : "Nunca sincronizado",
    };
  }
  if (device.storage_persisted === false) {
    return {
      family: "warning",
      dot: "hollow",
      label: "Almacenamiento sin proteger",
    };
  }
  return { family: "positive", dot: "solid", label: "Activo" };
}

export function DevicesSection({ me }: { me: Me }) {
  const grid = useLocalGrid({ sort: "location", order: "asc" });
  const [editing, setEditing] = useState<Device | null>(null);
  const [revoking, setRevoking] = useState<Device | null>(null);
  const [claiming, setClaiming] = useState(false);
  const [sede, setSede] = useState("");
  const [status, setStatus] = useState("");
  const locations = useLocations();
  const filtered = sede !== "" || status !== "";
  const devices = useDevices({
    page: grid.page,
    page_size: grid.pageSize,
    sort: grid.sort,
    order: grid.order,
    location_id: sede || undefined,
    status: (status || undefined) as "active" | "revoked" | undefined,
  });
  const revoke = useRevokeDevice();
  const settings = useSyncSettings();
  const staleHours = settings.data?.stale_device_hours ?? 48;
  const toast = useToast();

  function clearFilters() {
    setSede("");
    setStatus("");
    grid.resetToFirstPage();
  }

  if (devices.isError) {
    return (
      <RegionError
        title="No pudimos cargar los equipos."
        detail={
          devices.error instanceof ApiError && devices.error.status > 0
            ? devices.error.message
            : "Esta pantalla necesita conexión. La oficina lee el servidor, no una copia local."
        }
        requestId={
          devices.error instanceof ApiError
            ? devices.error.requestId
            : undefined
        }
        onRetry={() => void devices.refetch()}
      />
    );
  }

  return (
    <section className="flex h-full min-h-0 flex-col overflow-y-auto">
      <SectionHeading
        title="Equipos"
        description="Cada caja que vende es un equipo registrado. Aquí se ve cuál está en qué sede, cuándo sincronizó por última vez y si su navegador puede borrar lo que todavía no envió."
        action={
          /* Scope 2 · **the one place an office identity claims a browser**, and
             it cannot live only on the empty state: a network with seven tills
             already registered still has an eighth to prepare, and the empty
             state never mounts again after the first one. */
          <Button variant="secondary" onClick={() => setClaiming(true)}>
            Registrar este equipo
          </Button>
        }
      />

      <FilterBar>
        <Select
          value={sede}
          placeholder="Todas las sedes"
          aria-label="Filtrar por sede"
          options={(locations.data ?? []).map((one) => ({
            value: one.id,
            label: one.name,
          }))}
          onValueChange={(value) => {
            setSede(value);
            grid.resetToFirstPage();
          }}
        />
        <Select
          value={status}
          placeholder="Todos los estados"
          aria-label="Filtrar por estado"
          options={[
            { value: "active", label: "Activo" },
            { value: "revoked", label: "Dado de baja" },
          ]}
          onValueChange={(value) => {
            setStatus(value);
            grid.resetToFirstPage();
          }}
        />
      </FilterBar>

      <DataTable<Device>
        rows={devices.data?.rows ?? []}
        rowId={(row) => row.id}
        density="compact"
        minWidth={860}
        loading={devices.isPending}
        refetching={devices.isFetching && !devices.isPending}
        skeletonRows={7}
        skeletonWidths={["50%", "40%", "60%", "50%", "40%", "60%", "20%"]}
        sort={grid.sort}
        order={grid.order}
        onSort={grid.toggleSort}
        rowProps={(row) => ({ onClick: () => setEditing(row) })}
        empty={
          filtered ? (
            // §B.10.2 · a filtered empty state echoes the filters back and
            // offers `Quitar filtros` as a **secondary**, because the intent
            // was to filter.
            <EmptyState
              kind="filtered"
              title="Ningún equipo coincide con estos filtros"
              body={`Filtros activos: ${[
                sede
                  ? `sede ${locations.data?.find((one) => one.id === sede)?.name ?? ""}`
                  : null,
                status
                  ? `estado ${status === "active" ? "Activo" : "Dado de baja"}`
                  : null,
              ]
                .filter(Boolean)
                .join(` ${DOT} `)}.`}
              actionLabel="Quitar filtros"
              onAction={clearFilters}
            />
          ) : (
            <EmptyState
              title="Todavía no hay equipos registrados"
              body="Un equipo se registra en el mostrador, la primera vez que un cajero inicia sesión en él."
              actionLabel="Registrar este equipo"
              onAction={() => setClaiming(true)}
            />
          )
        }
        columns={[
          {
            key: "label",
            label: "Equipo",
            width: "20%",
            sortable: true,
            render: (row) => <span className="text-ink">{row.label}</span>,
          },
          {
            key: "location",
            label: "Sede",
            width: "14%",
            sortable: true,
            render: (row) => row.location_name,
          },
          {
            key: "last_synced_at",
            label: "Última sincronización",
            width: "16%",
            sortable: true,
            render: (row) =>
              row.last_synced_at ? (
                since(row.last_synced_at)
              ) : (
                // §B.9.2 tier 3 · never a zero, and never a last-known figure
                // without its reason.
                <span className="text-ink-soft">— nunca</span>
              ),
          },
          {
            key: "storage",
            label: "Almacenamiento",
            width: "14%",
            render: (row) =>
              row.storage_persisted === null ? (
                <span className="text-ink-soft">— sin reportar</span>
              ) : row.storage_persisted ? (
                "Protegido"
              ) : (
                "Sin proteger"
              ),
          },
          {
            key: "app_version",
            label: "Versión",
            width: "10%",
            sortable: true,
            render: (row) =>
              row.app_version || <span className="text-ink-soft">—</span>,
          },
          {
            key: "status",
            label: "Estado",
            width: "14%",
            sortable: true,
            render: (row) => {
              const state = meaning(row, staleHours);
              return (
                <Badge family={state.family} dot={state.dot}>
                  {state.label}
                </Badge>
              );
            },
          },
          {
            key: "actions",
            label: "",
            width: "12%",
            align: "right",
            render: (row) => (
              <Button
                variant="ghost"
                size="xs"
                onClick={(event) => {
                  event.stopPropagation();
                  setEditing(row);
                }}
              >
                Abrir
              </Button>
            ),
          },
        ]}
        footer={
          <TableFooter
            page={grid.page}
            pageSize={grid.pageSize}
            rowCount={devices.data?.row_count}
            onPage={grid.setPage}
            onPageSize={grid.setPageSize}
            loading={devices.isPending}
          />
        }
      />

      <div className="mt-8 border-t border-hairline pt-6">
        <SyncSettingsBlock me={me} />
      </div>

      {claiming ? (
        <ClaimThisBrowser me={me} onClose={() => setClaiming(false)} />
      ) : null}

      {editing ? (
        <DeviceRecord
          device={editing}
          onClose={() => setEditing(null)}
          onRevoke={() => {
            setRevoking(editing);
            setEditing(null);
          }}
        />
      ) : null}

      {/* §B.8.5 · the destructive confirm's button says what it does, never
          `Aceptar`. */}
      <ConfirmDialog
        open={revoking !== null}
        title={`¿Dar de baja ${revoking?.label ?? ""}?`}
        body={
          "Este equipo dejará de sincronizar en su próximo intento. Conserva su " +
          "catálogo y lo que todavía no ha enviado, y la oficina sigue viendo " +
          "cuántas filas quedaron pendientes."
        }
        confirmLabel="Dar de baja"
        busy={revoke.isPending}
        onCancel={() => setRevoking(null)}
        onConfirm={() => {
          const target = revoking;
          if (!target) return;
          revoke.mutate(target.id, {
            onSuccess: () => {
              toast(`${target.label} quedó dado de baja.`);
              setRevoking(null);
            },
            onError: (failure) =>
              toast(
                failure instanceof ApiError
                  ? failure.message
                  : "No pudimos dar de baja este equipo.",
              ),
          });
        }}
      />
    </section>
  );
}

function DeviceRecord({
  device,
  onClose,
  onRevoke,
}: {
  device: Device;
  onClose: () => void;
  onRevoke: () => void;
}) {
  const locations = useLocations();
  const save = useUpdateDevice();
  const toast = useToast();
  const [label, setLabel] = useState(device.label);
  const [locationId, setLocationId] = useState(device.location_id);
  const conflicts = useSyncConflicts({
    page: 1,
    page_size: 25,
    device_id: device.id,
    status: "open",
  });
  const skewSeconds =
    device.clock_skew_ms === null
      ? null
      : Math.round(device.clock_skew_ms / 1000);

  return (
    <Modal open title={device.label} onClose={onClose}>
      <div className="flex flex-col gap-4">
        <Field label="Nombre del equipo" htmlFor="device-rename">
          <Input
            id="device-rename"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </Field>
        <Field
          label="Sede"
          htmlFor="device-move"
          help="Cambiar de sede reinicia los precios que este equipo tiene descargados. No hay que volver a registrarlo."
        >
          <Select
            id="device-move"
            value={locationId}
            options={(locations.data ?? []).map((one) => ({
              value: one.id,
              label: one.name,
            }))}
            onValueChange={setLocationId}
          />
        </Field>

        <dl className="flex flex-col gap-2 border-t border-hairline pt-4 text-12">
          <Row label="Última sincronización">
            {device.last_synced_at ? since(device.last_synced_at) : "— nunca"}
          </Row>
          <Row label="Último envío">
            {device.last_pushed_at ? since(device.last_pushed_at) : "— nunca"}
          </Row>
          <Row label="Versión">{device.app_version || "—"}</Row>
          <Row label="Registrado por">{device.enrolled_by_name ?? "—"}</Row>
          <Row label="Reloj">
            {skewSeconds === null
              ? "— sin reportar"
              : `${skewSeconds >= 0 ? "+" : ""}${skewSeconds} s frente al servidor`}
          </Row>
          <Row label="Conflictos abiertos">
            {conflicts.data ? conflicts.data.row_count : "—"}
          </Row>
        </dl>

        {conflicts.data && conflicts.data.rows.length > 0 ? (
          <ul className="flex flex-col gap-1.5 border-t border-hairline pt-4">
            {conflicts.data.rows.map((row) => (
              <li key={row.id} className="text-12 text-ink-body">
                {CONFLICT_LABELS[row.type] ?? row.type} {DOT}{" "}
                {since(row.recorded_at)}
              </li>
            ))}
          </ul>
        ) : null}

        <div className="flex items-center justify-between gap-3 border-t border-hairline pt-4">
          <Button
            variant="destructive"
            size="sm"
            disabled={device.status === "revoked"}
            onClick={onRevoke}
          >
            Dar de baja
          </Button>
          <Button
            variant="primary"
            size="md"
            busy={save.isPending}
            busyLabel="Guardando…"
            onClick={() =>
              save.mutate(
                { id: device.id, label: label.trim(), location_id: locationId },
                {
                  onSuccess: () => {
                    toast("Equipo actualizado.");
                    onClose();
                  },
                  onError: (failure) =>
                    toast(
                      failure instanceof ApiError
                        ? failure.message
                        : "No pudimos guardar este equipo.",
                    ),
                },
              )
            }
          >
            Guardar cambios
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/**
 * Scope 2 · the one place an office identity claims a browser.
 *
 * **It is never offered anywhere else** (A4): the claim card belongs to a
 * `cashier` signing in at a counter, and an owner who means to prepare a till
 * comes here and says so. The record lands in this browser's local storage, so
 * the cashier who signs in next finds it already enrolled — and nothing
 * replicates into the owner's own session.
 */
function ClaimThisBrowser({ me, onClose }: { me: Me; onClose: () => void }) {
  const locations = useLocations();
  const claim = useClaimDevice();
  const toast = useToast();
  const [label, setLabel] = useState("");
  const [locationId, setLocationId] = useState("");

  return (
    <Modal open title="Registrar este equipo" onClose={onClose}>
      <div className="flex flex-col gap-4">
        <p className="text-12 text-ink-label">
          Este navegador quedará registrado como una caja. Descargará el
          catálogo cuando un cajero inicie sesión aquí; su sesión de{" "}
          {me.role === "owner" ? "propietaria" : "administradora"} no descarga
          nada.
        </p>
        <Field label="Nombre del equipo" htmlFor="claim-label">
          <Input
            id="claim-label"
            value={label}
            placeholder="Caja 1"
            autoFocus
            onChange={(event) => setLabel(event.target.value)}
          />
        </Field>
        <Field label="Sede" htmlFor="claim-sede">
          <Select
            id="claim-sede"
            value={locationId}
            placeholder="Elija una sede"
            options={(locations.data ?? []).map((one) => ({
              value: one.id,
              label: one.name,
            }))}
            onValueChange={setLocationId}
          />
        </Field>
        <div className="flex justify-end gap-3">
          <Button variant="secondary" size="md" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            size="md"
            busy={claim.isPending}
            busyLabel="Registrando…"
            disabled={!label.trim() || !locationId}
            onClick={() => {
              void (async () => {
                const persisted = await requestPersistence();
                claim.mutate(
                  { label: label.trim(), location_id: locationId, persisted },
                  {
                    onSuccess: (answer) => {
                      writeDevice({
                        ...recordFromClaim(answer),
                        persisted,
                        persistence_dialog_seen: persisted !== false,
                      });
                      toast(
                        `${answer.device.label} quedó registrado en este navegador.`,
                      );
                      onClose();
                    },
                    onError: (failure) =>
                      toast(
                        failure instanceof ApiError
                          ? failure.message
                          : "No pudimos registrar este equipo.",
                      ),
                  },
                );
              })();
            }}
          >
            Registrar equipo
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-11 text-ink-label">{label}</dt>
      <dd className="text-12 text-ink">{children}</dd>
    </div>
  );
}

const NUMBERS: {
  key: keyof SyncSettings;
  label: string;
  help: string;
}[] = [
  {
    key: "pull_interval_seconds",
    label: "Intervalo de descarga (s)",
    help: "Cada cuánto pregunta una caja si hay novedades, con la pestaña visible.",
  },
  {
    key: "pull_page_size",
    label: "Filas por página",
    help: "Cuántas filas trae cada descarga de una colección.",
  },
  {
    key: "push_batch_max_rows",
    label: "Filas por envío",
    help: "Cuántas filas manda una caja en un solo lote.",
  },
  {
    key: "push_batch_max_bytes",
    label: "Bytes por envío",
    help: "Tamaño máximo de un lote. Un lote más grande se rechaza entero.",
  },
  {
    key: "pull_safety_horizon_seconds",
    label: "Margen de seguridad (s)",
    help: "Las filas escritas hace menos de esto no se sirven todavía, para que ninguna se pierda entre dos transacciones.",
  },
  {
    key: "local_retention_days",
    label: "Retención local (días)",
    help: "Cuánto guarda una caja lo ya confirmado. Nada sin enviar se borra, a ninguna edad.",
  },
  {
    key: "clock_skew_warn_seconds",
    label: "Aviso de reloj (s)",
    help: "A partir de esta diferencia con el servidor, la caja y la oficina lo dicen.",
  },
  {
    key: "customer_recency_months",
    label: "Clientes recientes (meses)",
    help: "Qué parte de la lista de clientes cabe en una caja.",
  },
  {
    key: "stale_device_hours",
    label: "Equipo callado (horas)",
    help: "A partir de aquí un equipo aparece como «sin sincronizar» y se levanta un conflicto diario.",
  },
];

function SyncSettingsBlock({ me }: { me: Me }) {
  const settings = useSyncSettings();
  const save = useSaveSyncSettings();
  const toast = useToast();
  const [draft, setDraft] = useState<SyncSettings | null>(null);
  const current = draft ?? settings.data ?? null;
  const editable = me.role === "owner" || me.role === "admin";

  if (settings.isError) {
    return (
      <RegionError
        title="No pudimos cargar los ajustes de sincronización."
        detail={
          settings.error instanceof ApiError && settings.error.status > 0
            ? settings.error.message
            : "Esta pantalla necesita conexión."
        }
        requestId={
          settings.error instanceof ApiError
            ? settings.error.requestId
            : undefined
        }
        onRetry={() => void settings.refetch()}
      />
    );
  }
  if (!current) return null;

  return (
    <div>
      <SectionHeading
        title="Sincronización"
        description="Cómo hablan las cajas con el servidor. Los valores por defecto sirven para una red con conexión intermitente; cámbielos solo con una medición detrás."
      />
      <div className="grid grid-cols-2 gap-4">
        {NUMBERS.map((one) => (
          <Field
            key={one.key}
            label={one.label}
            help={one.help}
            htmlFor={one.key}
          >
            <Input
              id={one.key}
              type="number"
              inputMode="numeric"
              disabled={!editable}
              value={String(current[one.key])}
              onChange={(event) =>
                setDraft({
                  ...current,
                  [one.key]: Number(event.target.value),
                })
              }
            />
          </Field>
        ))}
        <Field
          label="Almacenamiento protegido"
          htmlFor="storage_persistence_policy"
          help="«Obligatorio» impide registrar un equipo cuyo navegador no conceda almacenamiento persistente."
        >
          <Select
            id="storage_persistence_policy"
            value={current.storage_persistence_policy}
            disabled={!editable}
            options={[
              { value: "warn", label: "Avisar" },
              { value: "required", label: "Obligatorio" },
            ]}
            onValueChange={(value) =>
              setDraft({
                ...current,
                storage_persistence_policy: value as "warn" | "required",
              })
            }
          />
        </Field>
      </div>
      {editable ? (
        <div className="mt-5 flex justify-end">
          <Button
            variant="primary"
            size="md"
            busy={save.isPending}
            busyLabel="Guardando…"
            disabled={draft === null}
            onClick={() =>
              save.mutate(current, {
                onSuccess: () => {
                  setDraft(null);
                  toast("Ajustes de sincronización guardados.");
                },
                onError: (failure) =>
                  toast(
                    failure instanceof ApiError
                      ? failure.message
                      : "No pudimos guardar los ajustes.",
                  ),
              })
            }
          >
            Guardar cambios
          </Button>
        </div>
      ) : null}
    </div>
  );
}
