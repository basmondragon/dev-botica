import { Fragment, useState } from "react";
import { ApiError } from "@/api/client";
import { useAuditLog, type AuditRow } from "@/api/queries";
import { cn } from "@/ui/cn";
import { since } from "@/ui/format";
import { DataTable, TableFooter } from "@/ui/table";
import { EmptyState, RegionError } from "@/ui/states";
import { useLocalGrid } from "@/ui/use-grid";
import { useListKeys } from "@/ui/use-list-keys";
import { SectionHeading } from "./section";

/** The closed audit vocabulary, in the interface's own words. */
const ACTION: Record<string, string> = {
  create: "Creó",
  update: "Modificó",
  delete: "Eliminó",
  archive: "Archivó",
  approve: "Aprobó",
  reject: "Rechazó",
  send: "Envió",
  revoke: "Revocó",
  impersonate: "Entró como",
};

const ENTITY: Record<string, string> = {
  tenants: "Droguería",
  locations: "Sede",
  users: "Persona",
  invitations: "Invitación",
  audit_log: "Actividad",
};

/**
 * §B.8.4·4 · **Ajustes · Actividad** -- the append-only trail at **compact
 * density**, 40px rows, which §B.4.1 names for exactly this surface.
 *
 * This is the grid contract's first real consumer, server-paginated with
 * `row_count` from the API. Its page, size and sort state is component-local
 * rather than in search params: if the dialog wrote search params, `Escape`
 * would have to restore the underlying route's own params and would lose the
 * caller's filters.
 *
 * **No control on this surface writes, updates or deletes**, and the database
 * grant makes that structural rather than editorial.
 */
export function ActivitySection() {
  const grid = useLocalGrid({ sort: "created_at", order: "desc" });
  const trail = useAuditLog({
    page: grid.page,
    page_size: grid.pageSize,
    sort: grid.sort,
    order: grid.order,
  });
  const [expanded, setExpanded] = useState<string | null>(null);

  const rows = trail.data?.rows ?? [];
  const keys = useListKeys({
    rowCount: rows.length,
    rowId: (index) => `activity-row-${index}`,
    pageKey: grid.page,
    onOpen: (index) => {
      const row = rows[index];
      if (row) setExpanded((current) => (current === row.id ? null : row.id));
    },
    onEscape: () => {
      if (!expanded) return false;
      setExpanded(null);
      return true;
    },
    onNextPage: () => {
      const total = trail.data?.row_count ?? 0;
      if (grid.page * grid.pageSize >= total) return false;
      grid.setPage(grid.page + 1);
      return true;
    },
    onPreviousPage: () => {
      if (grid.page <= 1) return false;
      grid.setPage(grid.page - 1);
      return true;
    },
  });

  if (trail.isError) {
    return (
      <RegionError
        title="No pudimos cargar la actividad de la droguería."
        detail={
          trail.error instanceof ApiError
            ? trail.error.message
            : "El servidor no respondió."
        }
        requestId={
          trail.error instanceof ApiError ? trail.error.requestId : undefined
        }
        onRetry={() => void trail.refetch()}
      />
    );
  }

  return (
    <section className="flex h-full min-h-0 flex-col">
      <SectionHeading
        title="Actividad"
        description="Todo cambio hecho por un perfil con permisos queda aquí, y no se puede editar ni borrar."
      />

      <DataTable<AuditRow>
        rows={rows}
        rowId={(row) => row.id}
        density="compact"
        minWidth={720}
        loading={trail.isPending}
        refetching={trail.isFetching && !trail.isPending}
        containerProps={keys.containerProps}
        skeletonWidths={["50%", "60%", "40%", "40%"]}
        sort={grid.sort}
        order={grid.order}
        onSort={grid.toggleSort}
        rowProps={(row, index) => ({
          id: `activity-row-${index}`,
          cursor: keys.cursor === index,
          current: expanded === row.id,
          onClick: () =>
            setExpanded((current) => (current === row.id ? null : row.id)),
        })}
        empty={
          <EmptyState
            kind="deliberate"
            title="Todavía no hay actividad registrada"
            body="Aquí queda cada cambio que hace un perfil con permisos: invitaciones, cambios de perfil, y los datos de la droguería."
          />
        }
        footer={
          <TableFooter
            page={grid.page}
            pageSize={grid.pageSize}
            rowCount={trail.data?.row_count}
            loading={trail.isPending}
            onPage={grid.setPage}
            onPageSize={grid.setPageSize}
          />
        }
        columns={[
          {
            key: "created_at",
            label: "Cuándo",
            width: "20%",
            sortable: true,
            numeric: true,
            render: (row) => (
              <span className="tabular-nums">{since(row.created_at)}</span>
            ),
          },
          {
            key: "actor",
            label: "Quién",
            width: "32%",
            sortable: true,
            truncate: true,
            render: (row) => (
              <span className="truncate text-ink">
                {row.actor_name ?? row.actor_email}
              </span>
            ),
          },
          {
            key: "action",
            label: "Acción",
            width: "24%",
            sortable: true,
            render: (row) => ACTION[row.action] ?? row.action,
          },
          {
            key: "entity_type",
            label: "Entidad",
            width: "24%",
            sortable: true,
            render: (row) => ENTITY[row.entity_type] ?? row.entity_type,
          },
        ]}
      />

      {/* §B.8.4·4 · a row expands **in place**; it does not open the shell's
          440px record panel, because a panel that pushes a dialog has nowhere
          to push to. */}
      {expanded ? (
        <Detail row={rows.find((row) => row.id === expanded)} />
      ) : null}
    </section>
  );
}

function Detail({ row }: { row: AuditRow | undefined }) {
  if (!row) return null;
  return (
    <div className="mt-4 rounded-card border border-hairline p-4">
      <p className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
        Detalle del cambio
      </p>
      <div className="mt-3 grid grid-cols-2 gap-4">
        <Side title="Antes" value={row.before} />
        <Side title="Después" value={row.after} />
      </div>
      {row.request_id ? (
        <p className="mt-3 select-all font-mono text-10 uppercase tracking-eyebrow text-ink-label">
          {row.request_id}
        </p>
      ) : null}
    </div>
  );
}

function Side({
  title,
  value,
}: {
  title: string;
  value: Record<string, unknown> | null;
}) {
  return (
    <div className="min-w-0">
      <p className="text-12 text-ink-label">{title}</p>
      {value === null ? (
        <p className="mt-1.5 text-14 text-ink-soft">—</p>
      ) : (
        <dl className={cn("mt-1.5 flex flex-col gap-1")}>
          {Object.entries(value).map(([key, entry]) => (
            <Fragment key={key}>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-11 text-ink-label">{key}</dt>
                <dd className="min-w-0 truncate text-12 text-ink">
                  {String(entry ?? "—")}
                </dd>
              </div>
            </Fragment>
          ))}
        </dl>
      )}
    </div>
  );
}
