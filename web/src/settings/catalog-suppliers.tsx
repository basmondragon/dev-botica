import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import {
  useDeleteSupplier,
  useSaveSupplier,
  useSupplierItems,
  useSuppliers,
  type Supplier,
} from "@/api/catalog";
import type { Me } from "@/api/queries";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { count, money } from "@/ui/format";
import { ConfirmDialog } from "@/ui/panel";
import { DataTable } from "@/ui/table";
import { EmptyState, RegionError } from "@/ui/states";
import { useToast } from "@/ui/toast";
import { SectionHeading } from "./section";

/**
 * §B.8.4·4 · **Ajustes · Catálogo · Proveedores**.
 *
 * A supplier's detail is a form in the same pane, not a record panel. Its item
 * list is **read-only here** and links into the catalog grid: the writer of a
 * `supplier_items` row is the item editor, so one row has one editor.
 *
 * *If the pilot's buyer lives in this screen daily it earns a route under
 * Compras at S6, and that is the diff.*
 */
export function SuppliersSection({ me }: { me: Me }) {
  const suppliers = useSuppliers();
  const [open, setOpen] = useState<Supplier | "new" | null>(null);
  const [deleting, setDeleting] = useState<Supplier | null>(null);
  const remove = useDeleteSupplier();
  const toast = useToast();
  const owner = me.role === "owner" || me.role === "platform_admin";

  if (suppliers.isError) {
    return (
      <RegionError
        title="No pudimos cargar los proveedores."
        detail={
          suppliers.error instanceof ApiError
            ? suppliers.error.message
            : "El servidor no respondió."
        }
        requestId={
          suppliers.error instanceof ApiError
            ? suppliers.error.requestId
            : undefined
        }
        onRetry={() => void suppliers.refetch()}
      />
    );
  }

  if (open) {
    return (
      <SupplierForm
        supplier={open === "new" ? undefined : open}
        owner={owner}
        onDone={() => setOpen(null)}
      />
    );
  }

  return (
    <section className="flex h-full min-h-0 flex-col">
      <SectionHeading
        title="Proveedores"
        description="A quién le compra la red, en qué plazo y con qué tiempo de entrega."
        action={
          <Button variant="secondary" onClick={() => setOpen("new")}>
            Nuevo proveedor
          </Button>
        }
      />
      <DataTable<Supplier>
        rows={suppliers.data ?? []}
        rowId={(row) => row.id}
        density="compact"
        // §B.4.4 · the six drawn columns need more room than a settings pane
        // has, so the **frame** scrolls rather than the headers clipping. The
        // column set is the one the spec draws; the container is what gives.
        minWidth={980}
        loading={suppliers.isPending}
        skeletonRows={6}
        skeletonWidths={["50%", "30%", "40%", "30%", "20%", "20%"]}
        rowProps={(row) => ({ onClick: () => setOpen(row) })}
        empty={
          <EmptyState
            title="Todavía no hay proveedores"
            body="Los proveedores llegan con el catálogo durante la puesta en marcha. También puede crear uno a mano."
            actionLabel="Nuevo proveedor"
            onAction={() => setOpen("new")}
          />
        }
        columns={[
          {
            key: "name",
            label: "Proveedor",
            width: "30%",
            truncate: true,
            render: (row) => <span className="text-ink">{row.name}</span>,
          },
          {
            key: "nit",
            label: "NIT",
            width: "16%",
            render: (row) => row.nit || "—",
          },
          {
            key: "contact",
            label: "Contacto",
            width: "20%",
            truncate: true,
            render: (row) => row.contact || "—",
          },
          {
            key: "terms",
            label: "Plazo de pago",
            width: "14%",
            render: (row) => row.payment_terms || "—",
          },
          {
            key: "lead",
            label: "Días de entrega",
            width: "12%",
            align: "right",
            numeric: true,
            render: (row) =>
              row.lead_time_days === null
                ? "—"
                : `${count(row.lead_time_days)} días`,
          },
          {
            key: "items",
            label: "Referencias",
            width: "8%",
            align: "right",
            numeric: true,
            render: (row) => count(row.item_count),
          },
        ]}
      />

      {owner && suppliers.data && suppliers.data.length > 0 ? (
        <p className="mt-3 text-11 text-ink-note">
          Para eliminar un proveedor, ábralo. Un proveedor que surte referencias
          no se elimina hasta que esas referencias dejen de nombrarlo.
        </p>
      ) : null}

      <ConfirmDialog
        open={!!deleting}
        title="Eliminar este proveedor"
        body={deleting ? `${deleting.name} deja de existir en la red.` : ""}
        confirmLabel={deleting ? `Eliminar ${deleting.name}` : "Eliminar"}
        busy={remove.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting.id, {
            onSuccess: () => {
              toast("Se eliminó el proveedor.");
              setDeleting(null);
            },
            onError: (error) =>
              toast(
                error instanceof ApiError
                  ? error.message
                  : "No pudimos eliminar el proveedor.",
              ),
          })
        }
      />
    </section>
  );
}

function SupplierForm({
  supplier,
  owner,
  onDone,
}: {
  supplier?: Supplier;
  /** §B.8.3 · `DELETE /api/suppliers/{id}` is `owner` only, so an `admin` does
   *  not see the action at all rather than pressing one the server refuses. */
  owner: boolean;
  onDone: () => void;
}) {
  const save = useSaveSupplier();
  const remove = useDeleteSupplier();
  const links = useSupplierItems({ supplier_id: supplier?.id }, !!supplier);
  const toast = useToast();
  const [deleting, setDeleting] = useState(false);
  const [draft, setDraft] = useState({
    name: supplier?.name ?? "",
    nit: supplier?.nit ?? "",
    contact: supplier?.contact ?? "",
    payment_terms: supplier?.payment_terms ?? "",
    lead_time_days: supplier?.lead_time_days?.toString() ?? "",
  });

  const set = (key: keyof typeof draft, value: string) =>
    setDraft((current) => ({ ...current, [key]: value }));

  return (
    <section className="surface-scroll flex h-full min-h-0 flex-col overflow-y-auto">
      <SectionHeading
        title={supplier ? supplier.name : "Nuevo proveedor"}
        description="El plazo de pago y los días de entrega alimentan la orden sugerida de Compras."
        action={
          <Button variant="secondary" onClick={onDone}>
            Volver
          </Button>
        }
      />
      <div className="grid max-w-[720px] grid-cols-2 gap-4">
        <Field label="Nombre" htmlFor="supplier-name" required>
          <Input
            id="supplier-name"
            value={draft.name}
            onChange={(event) => set("name", event.currentTarget.value)}
          />
        </Field>
        <Field label="NIT" htmlFor="supplier-nit" optional>
          <Input
            id="supplier-nit"
            value={draft.nit}
            onChange={(event) => set("nit", event.currentTarget.value)}
          />
        </Field>
        <Field label="Contacto" htmlFor="supplier-contact" optional>
          <Input
            id="supplier-contact"
            value={draft.contact}
            onChange={(event) => set("contact", event.currentTarget.value)}
          />
        </Field>
        <Field label="Plazo de pago" htmlFor="supplier-terms" optional>
          <Input
            id="supplier-terms"
            value={draft.payment_terms}
            placeholder="30 días"
            onChange={(event) =>
              set("payment_terms", event.currentTarget.value)
            }
          />
        </Field>
        <Field
          label="Días de entrega"
          htmlFor="supplier-lead"
          optional
          help="Compras lo reescribe con el tiempo que la recepción realmente tarda."
        >
          <Input
            id="supplier-lead"
            type="number"
            min={0}
            value={draft.lead_time_days}
            onChange={(event) =>
              set("lead_time_days", event.currentTarget.value)
            }
          />
        </Field>
      </div>

      <div className="mt-5 flex items-center gap-2">
        <Button
          variant="primary"
          busy={save.isPending}
          busyLabel="Guardando…"
          onClick={() =>
            save.mutate(
              {
                id: supplier?.id,
                body: {
                  ...draft,
                  lead_time_days: draft.lead_time_days
                    ? Number(draft.lead_time_days)
                    : null,
                },
              },
              {
                onSuccess: () => {
                  toast("Se guardó el proveedor.");
                  onDone();
                },
                onError: (error) =>
                  toast(
                    error instanceof ApiError
                      ? error.message
                      : "No pudimos guardar el proveedor.",
                  ),
              },
            )
          }
        >
          Guardar
        </Button>
        {supplier && owner ? (
          <Button variant="destructive" onClick={() => setDeleting(true)}>
            Eliminar
          </Button>
        ) : null}
      </div>

      {supplier ? (
        <div className="mt-8">
          <SectionHeading
            title="Referencias que surte"
            description="Se editan desde la ficha del producto, para que una fila tenga un solo editor."
          />
          <DataTable
            rows={links.data ?? []}
            rowId={(row) => row.id}
            density="compact"
            loading={links.isPending}
            skeletonRows={5}
            skeletonWidths={["60%", "20%", "20%"]}
            empty={
              <EmptyState
                kind="deliberate"
                title="Este proveedor no surte ninguna referencia"
                body="Las referencias se enlazan desde la ficha del producto, en Inventario · Catálogo."
              />
            }
            columns={[
              {
                key: "item",
                label: "Producto",
                width: "60%",
                truncate: true,
                render: (row) => (
                  <Link
                    to="/inventory/catalog"
                    search={{ item: row.item_id }}
                    className="text-ink underline-offset-2 hover:underline"
                  >
                    {row.item_name}
                  </Link>
                ),
              },
              {
                key: "cost",
                label: "Costo",
                width: "20%",
                align: "right",
                numeric: true,
                render: (row) =>
                  row.cost === null ? "—" : money(Number(row.cost)),
              },
              {
                key: "preferred",
                label: "Preferido",
                width: "20%",
                render: (row) => (row.is_preferred ? "Sí" : "—"),
              },
            ]}
          />
        </div>
      ) : null}

      <ConfirmDialog
        open={deleting}
        title="Eliminar este proveedor"
        body={
          supplier
            ? `${supplier.name} deja de existir en la red. Si surte alguna referencia, la eliminación se rechaza y le decimos cuántas son.`
            : ""
        }
        confirmLabel={supplier ? `Eliminar ${supplier.name}` : "Eliminar"}
        busyLabel="Eliminando…"
        busy={remove.isPending}
        onCancel={() => setDeleting(false)}
        onConfirm={() =>
          supplier &&
          remove.mutate(supplier.id, {
            onSuccess: () => {
              toast("Se eliminó el proveedor.");
              setDeleting(false);
              onDone();
            },
            onError: (error) =>
              toast(
                error instanceof ApiError
                  ? error.message
                  : "No pudimos eliminar el proveedor.",
              ),
          })
        }
      />
    </section>
  );
}
