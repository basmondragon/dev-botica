import { useState } from "react";
import { ApiError } from "@/api/client";
import {
  useCustomers,
  useDeleteCustomer,
  useSaveCustomer,
  type Customer,
} from "@/api/catalog";
import type { Me } from "@/api/queries";
import { DOCUMENT_TYPES } from "@/catalog/vocabulary";
import { Button } from "@/ui/button";
import { Checkbox, Field, Input, Textarea } from "@/ui/field";
import { SearchField } from "@/ui/filter-bar";
import { ConfirmDialog, Modal } from "@/ui/panel";
import { Select } from "@/ui/select";
import { StatusLine } from "@/ui/status";
import { DataTable, TableFooter } from "@/ui/table";
import { EmptyState, RegionError } from "@/ui/states";
import { useLocalGrid } from "@/ui/use-grid";
import { useToast } from "@/ui/toast";
import { SectionHeading } from "./section";

/**
 * §B.8.4·4 · **Ajustes · Catálogo · Clientes**.
 *
 * A settings section rather than a route because §12 rules out a CRM, a loyalty
 * programme and a patient record: `customers` exists to identify the acquirer
 * on a fiscal document and to recognise a returning customer, and the till
 * creates them inline at S4.
 */
export function CustomersSection({ me }: { me: Me }) {
  const grid = useLocalGrid({ sort: "name", order: "asc" });
  const [term, setTerm] = useState("");
  const [editing, setEditing] = useState<Customer | "new" | null>(null);
  const [deleting, setDeleting] = useState<Customer | null>(null);
  const customers = useCustomers({
    q: term || undefined,
    page: grid.page,
    page_size: grid.pageSize,
    sort: grid.sort,
    order: grid.order,
  });
  const remove = useDeleteCustomer();
  const toast = useToast();
  const owner = me.role === "owner" || me.role === "platform_admin";

  if (customers.isError) {
    return (
      <RegionError
        title="No pudimos cargar los clientes."
        detail={
          customers.error instanceof ApiError
            ? customers.error.message
            : "El servidor no respondió."
        }
        requestId={
          customers.error instanceof ApiError
            ? customers.error.requestId
            : undefined
        }
        onRetry={() => void customers.refetch()}
      />
    );
  }

  return (
    <section className="flex h-full min-h-0 flex-col">
      <SectionHeading
        title="Clientes"
        description="Quién queda nombrado en el documento equivalente. No es un CRM ni una historia clínica."
        action={
          <Button variant="secondary" onClick={() => setEditing("new")}>
            Nuevo cliente
          </Button>
        }
      />
      <div className="pb-3">
        <SearchField
          value={term}
          placeholder="Buscar por nombre o documento"
          onChange={(next) => {
            setTerm(next);
            grid.resetToFirstPage();
          }}
        />
      </div>
      <DataTable<Customer>
        rows={customers.data?.rows ?? []}
        rowId={(row) => row.id}
        density="compact"
        // §B.4.4 · the five drawn columns need a little more room than a
        // settings pane has, so the frame scrolls rather than the headers
        // clipping.
        minWidth={760}
        loading={customers.isPending}
        refetching={customers.isFetching && !customers.isPending}
        skeletonRows={8}
        skeletonWidths={["60%", "40%", "40%", "50%", "30%", "20%"]}
        sort={grid.sort}
        order={grid.order}
        onSort={grid.toggleSort}
        // A row opens its own editor, the way a catalog row opens its panel --
        // which is what lets the last column hold one action instead of two.
        rowProps={(row) => ({ onClick: () => setEditing(row) })}
        empty={
          term ? (
            <EmptyState
              kind="filtered"
              title="Ningún cliente coincide con esta búsqueda"
              body={`Búsqueda activa: «${term}».`}
              actionLabel="Quitar filtros"
              onAction={() => setTerm("")}
            />
          ) : (
            <EmptyState
              title="Todavía no hay clientes"
              body="El mostrador registra un cliente cuando la venta necesita nombrar a alguien. También puede crear uno a mano."
              actionLabel="Nuevo cliente"
              onAction={() => setEditing("new")}
            />
          )
        }
        footer={
          <TableFooter
            page={grid.page}
            pageSize={grid.pageSize}
            rowCount={customers.data?.row_count}
            loading={customers.isPending}
            onPage={grid.setPage}
            onPageSize={grid.setPageSize}
          />
        }
        columns={[
          {
            key: "name",
            label: "Cliente",
            width: "24%",
            sortable: true,
            truncate: true,
            // §B.9.2 tier 3 · derived from the absent name and document, never
            // stored. `Cliente eliminado` is what a Ley 1581 erasure looks like.
            render: (row) =>
              row.erased ? (
                <span className="text-ink-soft">Cliente eliminado</span>
              ) : (
                <span className="text-ink">{row.name || "—"}</span>
              ),
          },
          {
            key: "document",
            label: "Documento",
            width: "20%",
            sortable: true,
            render: (row) =>
              row.document ? (
                `${row.document_type} ${row.document}`
              ) : (
                <span className="text-ink-soft">—</span>
              ),
          },
          {
            key: "phone",
            label: "Teléfono",
            width: "16%",
            truncate: true,
            render: (row) => row.phone || "—",
          },
          {
            key: "email",
            label: "Correo",
            width: "18%",
            truncate: true,
            render: (row) => row.email || "—",
          },
          {
            key: "data_consent",
            label: "Consentimiento",
            width: "14%",
            sortable: true,
            render: (row) =>
              row.erased ? (
                <span className="text-ink-soft">—</span>
              ) : (
                <StatusLine
                  family={row.data_consent ? "positive" : "neutral"}
                  dot={row.data_consent ? "solid" : "hollow"}
                  label={row.data_consent ? "Otorgado" : "Sin registrar"}
                />
              ),
          },
          {
            key: "actions",
            label: "",
            width: "12%",
            align: "right",
            // The row itself opens the editor, so this column holds one action
            // rather than two. §B.8.3 · it is **absent** for an `admin`, and
            // never rendered disabled.
            render: (row) => (
              <span className="flex justify-end">
                {owner && !row.erased ? (
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={(event) => {
                      event.stopPropagation();
                      setDeleting(row);
                    }}
                  >
                    Eliminar
                  </Button>
                ) : null}
              </span>
            ),
          },
        ]}
      />

      {/* Keyed, so the draft's `useState` initialisers re-run against the
          customer actually handed in. Without it the dialog keeps the state it
          was first mounted with, opens blank on an existing client, and saving
          erases that client's identity and consent -- the same remount idiom
          `ItemForm` uses. */}
      <CustomerDialog
        key={editing === "new" ? "new" : (editing?.id ?? "closed")}
        open={!!editing}
        customer={editing === "new" ? undefined : (editing ?? undefined)}
        onClose={() => setEditing(null)}
      />

      <ConfirmDialog
        open={!!deleting}
        title="Eliminar los datos de este cliente"
        body={
          deleting
            ? `Si ninguna venta nombra a ${deleting.name || "este cliente"}, la fila se elimina. Si alguna lo nombra, se borran sus datos y la fila queda: la venta no puede quedarse sin a quién nombró.`
            : ""
        }
        confirmLabel="Eliminar los datos"
        busyLabel="Eliminando…"
        busy={remove.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting.id, {
            onSuccess: (result) => {
              toast(result.detail);
              setDeleting(null);
            },
            onError: (error) =>
              toast(
                error instanceof ApiError
                  ? error.message
                  : "No pudimos eliminar este cliente.",
              ),
          })
        }
      />
    </section>
  );
}

function CustomerDialog({
  open,
  customer,
  onClose,
}: {
  open: boolean;
  customer?: Customer;
  onClose: () => void;
}) {
  const save = useSaveCustomer();
  const toast = useToast();
  const [draft, setDraft] = useState<{
    document_type: string;
    document: string;
    name: string;
    phone: string;
    email: string;
    address: string;
    notes: string;
    data_consent: boolean;
  }>({
    document_type: customer?.document_type ?? "",
    document: customer?.document ?? "",
    name: customer?.name ?? "",
    phone: customer?.phone ?? "",
    email: customer?.email ?? "",
    address: customer?.address ?? "",
    notes: customer?.notes ?? "",
    data_consent: customer?.data_consent ?? false,
  });

  const set = <K extends keyof typeof draft>(
    key: K,
    value: (typeof draft)[K],
  ) => setDraft((current) => ({ ...current, [key]: value }));

  return (
    <Modal
      open={open}
      title={customer ? "Editar el cliente" : "Nuevo cliente"}
      busy={save.isPending}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            busy={save.isPending}
            busyLabel="Guardando…"
            onClick={() =>
              save.mutate(
                {
                  id: customer?.id,
                  body: {
                    ...draft,
                    document_type:
                      draft.document_type as Customer["document_type"],
                  },
                },
                {
                  onSuccess: () => {
                    toast("Se guardó el cliente.");
                    onClose();
                  },
                  onError: (error) =>
                    toast(
                      error instanceof ApiError
                        ? error.message
                        : "No pudimos guardar el cliente.",
                    ),
                },
              )
            }
          >
            Guardar
          </Button>
        </>
      }
    >
      <div className="mt-4 grid grid-cols-2 gap-4">
        <Field label="Tipo de documento" htmlFor="customer-doc-type">
          <Select
            id="customer-doc-type"
            value={draft.document_type}
            placeholder="Sin documento"
            options={DOCUMENT_TYPES.map((one) => ({ ...one }))}
            onValueChange={(next) => set("document_type", next)}
          />
        </Field>
        <Field label="Documento" htmlFor="customer-doc" optional>
          <Input
            id="customer-doc"
            value={draft.document}
            onChange={(event) => set("document", event.currentTarget.value)}
          />
        </Field>
        <Field label="Nombre" htmlFor="customer-name" className="col-span-2">
          <Input
            id="customer-name"
            value={draft.name}
            onChange={(event) => set("name", event.currentTarget.value)}
          />
        </Field>
        <Field label="Teléfono" htmlFor="customer-phone" optional>
          <Input
            id="customer-phone"
            value={draft.phone}
            onChange={(event) => set("phone", event.currentTarget.value)}
          />
        </Field>
        <Field label="Correo" htmlFor="customer-email" optional>
          <Input
            id="customer-email"
            type="email"
            value={draft.email}
            onChange={(event) => set("email", event.currentTarget.value)}
          />
        </Field>
        <Field
          label="Dirección"
          htmlFor="customer-address"
          optional
          className="col-span-2"
        >
          <Input
            id="customer-address"
            value={draft.address}
            onChange={(event) => set("address", event.currentTarget.value)}
          />
        </Field>
        <Field
          label="Notas"
          htmlFor="customer-notes"
          optional
          className="col-span-2"
        >
          <Textarea
            id="customer-notes"
            value={draft.notes}
            onChange={(event) => set("notes", event.currentTarget.value)}
          />
        </Field>
        <div className="col-span-2">
          <Checkbox
            checked={draft.data_consent}
            label="Autorizó el tratamiento de sus datos"
            onChange={(next) => set("data_consent", next)}
          />
          <p className="mt-1.5 text-11 text-ink-soft">
            Botica guarda el momento en que se otorgó, porque la Ley 1581 lo
            pregunta.
          </p>
        </div>
      </div>
    </Modal>
  );
}
