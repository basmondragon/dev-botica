import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import {
  useCategories,
  useDeleteCategory,
  useDeleteManufacturer,
  useManufacturers,
  useSaveCategory,
  useSaveManufacturer,
  type Category,
  type Manufacturer,
} from "@/api/catalog";
import type { Me } from "@/api/queries";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { count } from "@/ui/format";
import { ConfirmDialog, Modal } from "@/ui/panel";
import { Select } from "@/ui/select";
import { DataTable } from "@/ui/table";
import { EmptyState, RegionError } from "@/ui/states";
import { useToast } from "@/ui/toast";
import { SectionHeading } from "./section";

/**
 * §B.8.4·4 · **Ajustes · Catálogo · Laboratorios y categorías**.
 *
 * Two blocks in one scrolling pane. Compact density, 40px rows, with
 * component-local state -- a dialog does not own the address bar it floats
 * over. The reference count is a link into the catalog grid pre-filtered, so a
 * reference list is never a dead end.
 */
export function TaxonomySection({ me }: { me: Me }) {
  const manufacturers = useManufacturers();
  const categories = useCategories();
  const owner = me.role === "owner" || me.role === "platform_admin";

  if (manufacturers.isError || categories.isError) {
    const failure = (manufacturers.error ?? categories.error) as unknown;
    return (
      <RegionError
        title="No pudimos cargar los laboratorios y las categorías."
        detail={
          failure instanceof ApiError
            ? failure.message
            : "El servidor no respondió."
        }
        requestId={failure instanceof ApiError ? failure.requestId : undefined}
        onRetry={() => {
          void manufacturers.refetch();
          void categories.refetch();
        }}
      />
    );
  }

  return (
    <div className="surface-scroll flex h-full min-h-0 flex-col gap-8 overflow-y-auto">
      <Laboratorios
        rows={manufacturers.data ?? []}
        loading={manufacturers.isPending}
        owner={owner}
      />
      <Categorias
        rows={categories.data ?? []}
        loading={categories.isPending}
        owner={owner}
      />
    </div>
  );
}

function Laboratorios({
  rows,
  loading,
  owner,
}: {
  rows: Manufacturer[];
  loading: boolean;
  owner: boolean;
}) {
  const [editing, setEditing] = useState<Manufacturer | "new" | null>(null);
  const [deleting, setDeleting] = useState<Manufacturer | null>(null);
  const save = useSaveManufacturer();
  const remove = useDeleteManufacturer();
  const toast = useToast();
  const [name, setName] = useState("");
  const [nit, setNit] = useState("");

  function open(row: Manufacturer | "new") {
    setEditing(row);
    setName(row === "new" ? "" : row.name);
    setNit(row === "new" ? "" : row.nit);
  }

  return (
    <section className="flex flex-col">
      <SectionHeading
        title="Laboratorios"
        description="El laboratorio que fabrica cada referencia del catálogo."
        action={
          <Button variant="secondary" onClick={() => open("new")}>
            Nuevo laboratorio
          </Button>
        }
      />
      <DataTable<Manufacturer>
        rows={rows}
        rowId={(row) => row.id}
        density="compact"
        // §B.4.4 · below this the frame scrolls rather than the headers
        // clipping. A settings pane is narrower than a route, and a header that
        // overlaps the value beside it is worse than a horizontal scrollbar.
        minWidth={640}
        loading={loading}
        skeletonRows={6}
        skeletonWidths={["50%", "40%", "20%", "20%"]}
        empty={
          <EmptyState
            title="Todavía no hay laboratorios"
            body="Los laboratorios llegan con el catálogo. También puede crear uno a mano."
            actionLabel="Nuevo laboratorio"
            onAction={() => open("new")}
          />
        }
        columns={[
          {
            key: "name",
            label: "Laboratorio",
            width: "36%",
            truncate: true,
            render: (row) => <span className="text-ink">{row.name}</span>,
          },
          {
            key: "nit",
            label: "NIT",
            width: "24%",
            render: (row) => row.nit || "—",
          },
          {
            key: "items",
            label: "Referencias",
            width: "18%",
            align: "right",
            numeric: true,
            render: (row) => (
              <Link
                to="/inventory/catalog"
                search={{ manufacturer_id: row.id }}
                className="text-ink-body underline-offset-2 hover:text-ink hover:underline"
              >
                {count(row.item_count)}
              </Link>
            ),
          },
          {
            key: "actions",
            label: "",
            width: "24%",
            align: "right",
            render: (row) => (
              <span className="flex justify-end gap-1">
                <Button size="xs" variant="ghost" onClick={() => open(row)}>
                  Editar
                </Button>
                {owner ? (
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => setDeleting(row)}
                  >
                    Eliminar
                  </Button>
                ) : null}
              </span>
            ),
          },
        ]}
      />

      <Modal
        open={!!editing}
        title={
          editing === "new" ? "Nuevo laboratorio" : "Editar el laboratorio"
        }
        busy={save.isPending}
        onClose={() => setEditing(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditing(null)}>
              Cancelar
            </Button>
            <Button
              variant="primary"
              busy={save.isPending}
              busyLabel="Guardando…"
              onClick={() =>
                save.mutate(
                  {
                    id: editing && editing !== "new" ? editing.id : undefined,
                    body: { name, nit },
                  },
                  {
                    onSuccess: () => {
                      toast("Se guardó el laboratorio.");
                      setEditing(null);
                    },
                    onError: (error) =>
                      toast(
                        error instanceof ApiError
                          ? error.message
                          : "No pudimos guardar el laboratorio.",
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
        <div className="mt-4 flex flex-col gap-4">
          <Field label="Nombre" htmlFor="lab-name" required>
            <Input
              id="lab-name"
              value={name}
              onChange={(event) => setName(event.currentTarget.value)}
            />
          </Field>
          <Field label="NIT" htmlFor="lab-nit" optional>
            <Input
              id="lab-nit"
              value={nit}
              onChange={(event) => setNit(event.currentTarget.value)}
            />
          </Field>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!deleting}
        title="Eliminar este laboratorio"
        body={
          deleting
            ? `${deleting.name} desaparece del catálogo y de sus filtros. Si alguna referencia lo nombra, la eliminación se rechaza y le decimos cuántas son.`
            : ""
        }
        confirmLabel={deleting ? `Eliminar ${deleting.name}` : "Eliminar"}
        busyLabel="Eliminando…"
        busy={remove.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting.id, {
            onSuccess: () => {
              toast("Se eliminó el laboratorio.");
              setDeleting(null);
            },
            onError: (error) =>
              toast(
                error instanceof ApiError
                  ? error.message
                  : "No pudimos eliminar el laboratorio.",
              ),
          })
        }
      />
    </section>
  );
}

function Categorias({
  rows,
  loading,
  owner,
}: {
  rows: Category[];
  loading: boolean;
  owner: boolean;
}) {
  const [editing, setEditing] = useState<Category | "new" | null>(null);
  const [deleting, setDeleting] = useState<Category | null>(null);
  const save = useSaveCategory();
  const remove = useDeleteCategory();
  const toast = useToast();
  const [name, setName] = useState("");
  const [parent, setParent] = useState("");

  const parents = rows.filter((row) => !row.parent_id);

  function open(row: Category | "new") {
    setEditing(row);
    setName(row === "new" ? "" : row.name);
    setParent(row === "new" ? "" : (row.parent_id ?? ""));
  }

  return (
    <section className="flex flex-col">
      <SectionHeading
        title="Categorías"
        description="Dos niveles: suficientemente plano para filtrar, suficientemente anidado para agrupar."
        action={
          <Button variant="secondary" onClick={() => open("new")}>
            Nueva categoría
          </Button>
        }
      />
      <DataTable<Category>
        rows={rows}
        rowId={(row) => row.id}
        density="compact"
        minWidth={640}
        loading={loading}
        skeletonRows={6}
        skeletonWidths={["50%", "20%", "20%"]}
        empty={
          <EmptyState
            title="Todavía no hay categorías"
            body="Las categorías llegan con el catálogo. También puede crear una a mano."
            actionLabel="Nueva categoría"
            onAction={() => open("new")}
          />
        }
        columns={[
          {
            key: "name",
            label: "Categoría",
            width: "56%",
            truncate: true,
            render: (row) => (
              <span
                className={row.parent_id ? "pl-6 text-ink-body" : "text-ink"}
              >
                {row.name}
              </span>
            ),
          },
          {
            key: "items",
            label: "Referencias",
            width: "20%",
            align: "right",
            numeric: true,
            render: (row) => (
              <Link
                to="/inventory/catalog"
                search={{ category_id: row.id }}
                className="text-ink-body underline-offset-2 hover:text-ink hover:underline"
              >
                {count(row.item_count)}
              </Link>
            ),
          },
          {
            key: "actions",
            label: "",
            width: "24%",
            align: "right",
            render: (row) => (
              <span className="flex justify-end gap-1">
                <Button size="xs" variant="ghost" onClick={() => open(row)}>
                  Editar
                </Button>
                {owner ? (
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => setDeleting(row)}
                  >
                    Eliminar
                  </Button>
                ) : null}
              </span>
            ),
          },
        ]}
      />

      <Modal
        open={!!editing}
        title={editing === "new" ? "Nueva categoría" : "Editar la categoría"}
        busy={save.isPending}
        onClose={() => setEditing(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditing(null)}>
              Cancelar
            </Button>
            <Button
              variant="primary"
              busy={save.isPending}
              busyLabel="Guardando…"
              onClick={() =>
                save.mutate(
                  {
                    id: editing && editing !== "new" ? editing.id : undefined,
                    body: { name, parent_id: parent || null },
                  },
                  {
                    onSuccess: () => {
                      toast("Se guardó la categoría.");
                      setEditing(null);
                    },
                    onError: (error) =>
                      toast(
                        error instanceof ApiError
                          ? error.message
                          : "No pudimos guardar la categoría.",
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
        <div className="mt-4 flex flex-col gap-4">
          <Field label="Nombre" htmlFor="category-name" required>
            <Input
              id="category-name"
              value={name}
              onChange={(event) => setName(event.currentTarget.value)}
            />
          </Field>
          <Field
            label="Categoría madre"
            htmlFor="category-parent"
            optional
            help="Sin madre es una categoría de primer nivel. El catálogo tiene dos."
          >
            <Select
              id="category-parent"
              value={parent}
              placeholder="Sin madre"
              options={parents
                .filter((one) => editing === "new" || one.id !== editing?.id)
                .map((one) => ({ value: one.id, label: one.name }))}
              onValueChange={setParent}
            />
          </Field>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!deleting}
        title="Eliminar esta categoría"
        body={
          deleting
            ? `${deleting.name} desaparece del filtro de categorías. Si alguna referencia o subcategoría la nombra, la eliminación se rechaza y le decimos cuántas son.`
            : ""
        }
        confirmLabel={deleting ? `Eliminar ${deleting.name}` : "Eliminar"}
        busyLabel="Eliminando…"
        busy={remove.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting.id, {
            onSuccess: () => {
              toast("Se eliminó la categoría.");
              setDeleting(null);
            },
            onError: (error) =>
              toast(
                error instanceof ApiError
                  ? error.message
                  : "No pudimos eliminar la categoría.",
              ),
          })
        }
      />
    </section>
  );
}
