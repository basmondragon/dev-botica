import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import { ItemCombobox } from "@/catalog/item-combobox";
import {
  useCreateTransfer,
  useDispatchTransfer,
  useReceiveTransfer,
  useResolveTransfer,
  useTransfers,
  type TransferRow,
} from "@/api/inventory";
import { useLocations, type Me } from "@/api/queries";
import { Content, TableContent, TopBar, TopBarButton } from "@/shell/shell";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { count, since } from "@/ui/format";
import { Modal, RecordPanel } from "@/ui/panel";
import { Select } from "@/ui/select";
import { Badge } from "@/ui/status";
import { EmptyState, RegionError, RouteError } from "@/ui/states";
import { DataTable, TableFooter } from "@/ui/table";
import { useToast } from "@/ui/toast";
import { useListKeys } from "@/ui/use-list-keys";
import { InventoryBreadcrumb } from "./breadcrumb";
import { TRANSFER_STATUS } from "./vocabulary";

export interface TransfersSearch {
  page?: number;
  pageSize?: number;
  traslado?: string;
  nuevo?: boolean;
  settings?: string;
}

/**
 * §B.8.1 · **Inventario · Traslados.**
 *
 * Merchandise moves between sedes as a document with two ends and two moments,
 * so the box that left Chapinero on Tuesday and has not reached Suba by
 * Thursday is visible as exactly that rather than as a discrepancy nobody can
 * name. `partial` sorts first, because **that is the work list**.
 *
 * **Online-only in all three verbs.** A dispatch commits stock at another
 * location's expense, a transfer created twice offline is merchandise that
 * leaves twice, and a receipt is an append against a document the device has
 * never seen.
 */
export function TransfersPage({
  me,
  search,
}: {
  me: Me;
  search: TransfersSearch;
}) {
  const navigate = useNavigate();
  const elevated = me.role !== "cashier";
  const page = search.page ?? 1;
  const pageSize = search.pageSize ?? 25;
  const query = useTransfers({ page, page_size: pageSize });
  const rows = query.data?.rows ?? [];
  const open = rows.find((one) => one.id === search.traslado);

  const go = (next: Partial<TransfersSearch>) =>
    void navigate({
      to: "/inventory/transfers",
      search: (previous: TransfersSearch) => ({ ...previous, ...next }),
    });

  const keys = useListKeys({
    rowCount: rows.length,
    rowId: (index) => `transfer-row-${index}`,
    pageKey: String(page),
    // §B.13.2 · `j`, `k`, `Enter`, `Esc` and page turning. **`x` is
    // deliberately unbound**, for the reason S1 gave on Catálogo: it toggles a
    // row into a bulk-action set, and this one has none -- a transfer is
    // dispatched, received or resolved as a document, one at a time. Wiring a
    // selection model with nothing to do with it would be a control that does
    // nothing. The first stage that gives this list a bulk action passes
    // `onToggleCheck` and `x` starts working with no other change.
    onOpen: (index) => {
      const row = rows[index];
      if (row) go({ traslado: row.id });
    },
    onEscape: () => {
      if (!search.traslado) return false;
      go({ traslado: undefined });
      return true;
    },
  });

  if (query.isError) {
    return (
      <>
        <Header elevated={elevated} onNew={() => go({ nuevo: true })} />
        <Content>
          <RouteError
            title="No pudimos cargar los traslados."
            detail={
              query.error instanceof ApiError && query.error.status > 0
                ? query.error.message
                : "Los traslados necesitan conexión: un despacho compromete " +
                  "existencias de otra sede, así que no se registra desde una " +
                  "copia local."
            }
            requestId={
              query.error instanceof ApiError
                ? query.error.requestId
                : undefined
            }
            onRetry={() => void query.refetch()}
          />
        </Content>
      </>
    );
  }

  return (
    <>
      <Header elevated={elevated} onNew={() => go({ nuevo: true })} />
      <div className="flex min-h-0 flex-1">
        <TableContent>
          <DataTable<TransferRow>
            rows={rows}
            rowId={(row) => row.id}
            density="standard"
            minWidth={900}
            loading={query.isPending}
            refetching={query.isFetching && !query.isPending}
            skeletonRows={8}
            containerProps={keys.containerProps}
            rowProps={(row, index) => ({
              id: `transfer-row-${index}`,
              cursor: keys.cursor === index,
              current: search.traslado === row.id,
              onClick: () => go({ traslado: row.id }),
            })}
            empty={
              <EmptyState
                title="Todavía no hay traslados"
                body="Un traslado mueve mercancía entre dos sedes y se registra en dos momentos: al despachar y al recibir."
                actionLabel={elevated ? "Nuevo traslado" : undefined}
                onAction={elevated ? () => go({ nuevo: true }) : undefined}
              />
            }
            footer={
              <TableFooter
                page={page}
                pageSize={pageSize}
                rowCount={query.data?.row_count}
                loading={query.isPending}
                onPage={(next) => go({ page: next })}
                onPageSize={(next) => go({ pageSize: next, page: 1 })}
              />
            }
            columns={[
              {
                key: "number",
                label: "Traslado",
                width: "12%",
                render: (row) => (
                  <span className="tabular-nums text-ink">
                    Traslado {row.number}
                  </span>
                ),
              },
              {
                key: "origin",
                label: "Origen",
                width: "16%",
                truncate: true,
                render: (row) => row.origin_location_name,
              },
              {
                key: "destination",
                label: "Destino",
                width: "16%",
                truncate: true,
                render: (row) => row.destination_location_name,
              },
              {
                key: "references",
                label: "Referencias",
                width: "11%",
                align: "right",
                numeric: true,
                render: (row) => count(row.references),
              },
              {
                key: "dispatched",
                label: "Despachado",
                width: "13%",
                render: (row) =>
                  row.dispatched_at ? since(row.dispatched_at) : <Dash />,
              },
              {
                key: "received",
                label: "Recibido",
                width: "13%",
                render: (row) =>
                  row.received_at ? since(row.received_at) : <Dash />,
              },
              {
                key: "status",
                label: "Estado",
                width: "19%",
                render: (row) => {
                  const meaning = TRANSFER_STATUS[row.status];
                  return (
                    <Badge family={meaning.family} dot={meaning.dot}>
                      {meaning.label}
                      {row.in_transit > 0 ? (
                        <span className="text-ink-body">
                          {" "}
                          · {count(row.in_transit)} en tránsito
                        </span>
                      ) : null}
                    </Badge>
                  );
                },
              },
            ]}
          />
        </TableContent>

        {open ? (
          <TransferPanel
            transfer={open}
            me={me}
            onClose={() => go({ traslado: undefined })}
          />
        ) : null}
      </div>

      <NewTransfer
        open={!!search.nuevo && elevated}
        onClose={() => go({ nuevo: undefined })}
        onCreated={(id) => go({ nuevo: undefined, traslado: id })}
      />
    </>
  );
}

function Dash() {
  return <span className="text-ink-soft">—</span>;
}

function Header({ elevated, onNew }: { elevated: boolean; onNew: () => void }) {
  return (
    <TopBar
      breadcrumb={<InventoryBreadcrumb />}
      title="Traslados"
      actions={
        elevated ? (
          <TopBarButton variant="primary" onClick={onNew}>
            Nuevo traslado
          </TopBarButton>
        ) : null
      }
    />
  );
}

/**
 * The detail panel: the lines, the two moments, the two people, and -- on
 * `partial` -- **the two resolutions as buttons with the consequence written
 * under each**.
 */
function TransferPanel({
  transfer,
  me,
  onClose,
}: {
  transfer: TransferRow;
  me: Me;
  onClose: () => void;
}) {
  const elevated = me.role !== "cashier";
  const dispatch = useDispatchTransfer();
  const receive = useReceiveTransfer();
  const resolve = useResolveTransfer();
  const toast = useToast();
  const busy = dispatch.isPending || receive.isPending || resolve.isPending;
  const failure = dispatch.error ?? receive.error ?? resolve.error;

  // §B.8.3 · a `cashier` at the destination may press `Recibir` -- they are the
  // person opening the box. They may not create, dispatch or resolve.
  const mayReceive =
    transfer.status === "dispatched" &&
    (elevated || me.location_id === transfer.destination_location_id);

  return (
    <RecordPanel
      title={`Traslado ${transfer.number}`}
      open
      onClose={onClose}
      footer={
        <div className="flex flex-wrap items-center gap-2">
          {elevated && transfer.status === "draft" ? (
            <Button
              size="sm"
              variant="primary"
              busy={dispatch.isPending}
              onClick={() =>
                dispatch.mutate(transfer.id, {
                  onSuccess: () =>
                    toast(`Despachado desde ${transfer.origin_location_name}.`),
                })
              }
            >
              Despachar
            </Button>
          ) : null}
          {mayReceive ? (
            <Button
              size="sm"
              variant="primary"
              busy={receive.isPending}
              onClick={() =>
                receive.mutate(
                  { id: transfer.id },
                  {
                    onSuccess: () =>
                      toast(
                        `Recibido en ${transfer.destination_location_name}.`,
                      ),
                  },
                )
              }
            >
              Recibir
            </Button>
          ) : null}
        </div>
      }
    >
      <dl className="flex flex-col gap-1.5">
        <PanelRow label="Origen" value={transfer.origin_location_name} />
        <PanelRow label="Destino" value={transfer.destination_location_name} />
        <PanelRow
          label="Despachado"
          value={
            transfer.dispatched_at
              ? `${since(transfer.dispatched_at)}${
                  transfer.dispatched_by_name
                    ? ` · ${transfer.dispatched_by_name}`
                    : ""
                }`
              : "Todavía no"
          }
        />
        <PanelRow
          label="Recibido"
          value={
            transfer.received_at
              ? `${since(transfer.received_at)}${
                  transfer.received_by_name
                    ? ` · ${transfer.received_by_name}`
                    : ""
                }`
              : "Todavía no"
          }
        />
        <PanelRow
          label="En tránsito"
          value={
            transfer.in_transit > 0 ? (
              <span className="tabular-nums">
                {count(transfer.in_transit)} unidades
              </span>
            ) : (
              "Nada"
            )
          }
        />
      </dl>

      <section className="mt-5 border-t border-hairline pt-4">
        <h3 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
          Líneas
        </h3>
        <ul className="mt-3 flex flex-col gap-4">
          {transfer.lines.map((line) => (
            <li key={line.id}>
              <p className="text-14 text-ink">{line.item_name}</p>
              <p className="text-12 text-ink-label">
                {line.lot_code ? `Lote ${line.lot_code} · ` : ""}
                pedido {count(line.quantity_requested)} · despachado{" "}
                {count(line.quantity_dispatched)} · recibido{" "}
                {count(line.quantity_received)}
              </p>
              {elevated &&
              transfer.status === "partial" &&
              line.in_transit > 0 ? (
                <div className="mt-2.5 flex flex-col gap-2">
                  <Resolution
                    label="Llegó después"
                    consequence={`Se registra la entrada de ${count(
                      line.in_transit,
                    )} unidades en ${transfer.destination_location_name}.`}
                    busy={busy}
                    onClick={() =>
                      resolve.mutate({
                        id: transfer.id,
                        line_id: line.id,
                        resolution: "received_late",
                      })
                    }
                  />
                  <Resolution
                    label="No llegó"
                    consequence={`Se registra como pérdida en ${transfer.origin_location_name}. El total de la red baja ${count(
                      line.in_transit,
                    )} unidades, una sola vez.`}
                    busy={busy}
                    onClick={() =>
                      resolve.mutate({
                        id: transfer.id,
                        line_id: line.id,
                        resolution: "lost_in_transit",
                      })
                    }
                  />
                </div>
              ) : null}
              {line.resolution ? (
                <p className="mt-1.5 text-11 text-ink-label">
                  {line.resolution === "received_late"
                    ? "Resuelto: llegó después."
                    : "Resuelto: no llegó, registrado como pérdida en el origen."}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      {failure ? (
        <div className="mt-4">
          <RegionError
            title="No pudimos completar la operación."
            detail={(failure as Error).message}
            requestId={
              failure instanceof ApiError ? failure.requestId : undefined
            }
          />
        </div>
      ) : null}
    </RecordPanel>
  );
}

function Resolution({
  label,
  consequence,
  busy,
  onClick,
}: {
  label: string;
  consequence: string;
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <div>
      <Button size="sm" variant="secondary" busy={busy} onClick={onClick}>
        {label}
      </Button>
      <p className="mt-1 text-11 text-ink-label">{consequence}</p>
    </div>
  );
}

function PanelRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="shrink-0 text-12 text-ink-label">{label}</dt>
      <dd className="min-w-0 truncate text-right text-14 text-ink">{value}</dd>
    </div>
  );
}

interface DraftLine {
  item_id: string;
  item_name: string;
  lot_id: string | null;
  quantity: string;
}

function NewTransfer({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const locations = useLocations();
  const create = useCreateTransfer();
  const toast = useToast();
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [lines, setLines] = useState<DraftLine[]>([]);

  const valid =
    origin && destination && origin !== destination && lines.length > 0;

  return (
    <Modal
      open={open}
      title="Nuevo traslado"
      busy={create.isPending}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            size="sm"
            busy={create.isPending}
            disabled={!valid}
            onClick={() =>
              create.mutate(
                {
                  origin_location_id: origin,
                  destination_location_id: destination,
                  note: "",
                  lines: lines.map((line) => ({
                    item_id: line.item_id,
                    lot_id: line.lot_id,
                    quantity_requested: Number(line.quantity) || 0,
                  })),
                },
                {
                  onSuccess: (created) => {
                    toast(`Traslado ${created.number} creado como borrador.`);
                    setLines([]);
                    onCreated(created.id);
                  },
                },
              )
            }
          >
            Crear borrador
          </Button>
        </>
      }
    >
      <div className="mt-4 flex flex-col gap-4">
        <p className="text-12 text-ink-label">
          Un borrador no mueve nada: sus líneas son una solicitud y no una
          reserva. Las unidades salen del origen al despachar.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Sede de origen">
            <Select
              value={origin}
              onValueChange={setOrigin}
              options={[
                { value: "", label: "Elija una sede" },
                ...(locations.data ?? []).map((one) => ({
                  value: one.id,
                  label: one.name,
                })),
              ]}
            />
          </Field>
          <Field
            label="Sede de destino"
            error={
              origin && origin === destination
                ? "Un traslado va de una sede a otra distinta."
                : undefined
            }
          >
            <Select
              value={destination}
              onValueChange={setDestination}
              options={[
                { value: "", label: "Elija una sede" },
                ...(locations.data ?? []).map((one) => ({
                  value: one.id,
                  label: one.name,
                })),
              ]}
            />
          </Field>
        </div>

        <Field label="Agregar producto">
          <ItemCombobox
            value=""
            type="product"
            onChange={(_id, item) => {
              if (!item) return;
              setLines((current) => [
                ...current,
                {
                  item_id: item.id,
                  item_name: item.name,
                  lot_id: null,
                  quantity: "1",
                },
              ]);
            }}
          />
        </Field>

        {lines.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {lines.map((line, index) => (
              <li
                key={`${line.item_id}-${index}`}
                className="flex items-end gap-2"
              >
                <span className="min-w-0 flex-1 truncate text-14 text-ink">
                  {line.item_name}
                </span>
                <Input
                  inputMode="numeric"
                  aria-label={`Cantidad de ${line.item_name}`}
                  className="w-24"
                  value={line.quantity}
                  onChange={(event) =>
                    setLines((current) =>
                      current.map((one, position) =>
                        position === index
                          ? { ...one, quantity: event.currentTarget.value }
                          : one,
                      ),
                    )
                  }
                />
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setLines((current) =>
                      current.filter((_one, position) => position !== index),
                    )
                  }
                >
                  Quitar
                </Button>
              </li>
            ))}
          </ul>
        ) : null}

        {create.isError ? (
          <RegionError
            title="No pudimos crear el traslado."
            detail={(create.error as Error).message}
          />
        ) : null}
      </div>
    </Modal>
  );
}
