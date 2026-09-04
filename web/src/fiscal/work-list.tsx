import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import {
  useFiscalDocument,
  useFiscalDocuments,
  useFiscalSummary,
  useRetryFiscalDocument,
  useUnsentSales,
  type FiscalDocumentRow,
  type FiscalSummary,
  type UnsentSaleRow,
} from "@/api/fiscal";
import { useLocations, type Me } from "@/api/queries";
import { useSettingsDialog } from "@/settings/use-settings";
import { Content, TableContent, TopBar } from "@/shell/shell";
import { Button } from "@/ui/button";
import { ChipOptions, FilterBar, FilterChip } from "@/ui/filter-bar";
import { DOT, count, since } from "@/ui/format";
import { RecordPanel } from "@/ui/panel";
import { Segmented } from "@/ui/segmented";
import { Badge } from "@/ui/status";
import { EmptyState, ProgressLine, RegionError, RouteError } from "@/ui/states";
import { DataTable, TableFooter } from "@/ui/table";
import { useToast } from "@/ui/toast";
import { useListKeys } from "@/ui/use-list-keys";
import {
  DOCUMENT_TYPE,
  FISCAL_STATUS,
  SEGMENTS,
  WORK_LIST,
  type Segment,
} from "./vocabulary";

export interface FiscalSearch {
  page?: number;
  pageSize?: number;
  lista?: Segment;
  sede?: string;
  envio?: string;
  settings?: string;
}

/**
 * §B.8.1 · **`Envíos a facturación` — the work list.**
 *
 * An office route in Existencias' shape: filter bar, table, record panel. **It
 * has no nav item** — §B.8.1 caps the sidebar at seven and Botica is at it — so
 * it is reached from three places: the Panel's strip (S9), a `Ver envíos`
 * action in the settings section, and the link in the digest email. *The
 * measurement that would change that:* if a pilot's administrator opens it more
 * than weekly, it earns a nav item.
 *
 * **Unconfigured the route still resolves**, and renders §B.10.2's
 * never-populated empty state in the neutral family pointing at the settings
 * section — never a count, never a warning family, never a badge (§8). A route
 * that refused would be indistinguishable from a broken link.
 */
export function FiscalWorkList({
  me,
  search,
}: {
  me: Me;
  search: FiscalSearch;
}) {
  const navigate = useNavigate();
  const settings = useSettingsDialog();
  const segment: Segment = search.lista ?? "pendientes";
  const page = search.page ?? 1;
  const pageSize = search.pageSize ?? 25;
  const locations = useLocations();
  // **The empty state's kind is decided by `configured`, not by the row
  // count.** An empty list means one of two opposite things -- nothing is
  // connected, or everything has settled -- and §B.10.2 makes conflating them
  // the defect. The summary is the one endpoint that answers which, and
  // unconfigured it answers with no counts at all (§8).
  const summary = useFiscalSummary();

  const go = (next: Partial<FiscalSearch>) =>
    void navigate({
      to: "/fiscal-documents",
      search: (previous: FiscalSearch) => ({ ...previous, ...next }),
    });

  const documents = useFiscalDocuments(
    {
      page,
      page_size: pageSize,
      order: "desc",
      sort: "created_at",
      // **The segment is a server filter.** Filtering a page the server has
      // already cut would show an arbitrary subset of it and leave the footer
      // counting rows that are not there.
      status: segment === "fallidos" ? ["failed"] : ["pending", "sent"],
      location_id: search.sede ? [search.sede] : undefined,
    },
    segment !== "sin-enviar",
  );
  const unsent = useUnsentSales(
    { page, page_size: pageSize },
    segment === "sin-enviar",
  );

  const query = segment === "sin-enviar" ? unsent : documents;
  // `Pendientes` is everything that has not settled — `pending` **and**
  // `sent` — and the server answers exactly that set. An administrator asks *is
  // anything stuck*, and both states answer yes.
  const shown = query.data?.rows ?? [];

  const keys = useListKeys({
    rowCount: shown.length,
    rowId: (index) => `fiscal-row-${index}`,
    pageKey: `${segment}-${page}`,
    // §B.13.2 · `j`, `k`, `Enter`, `Esc` and page turning. **`x` is
    // deliberately unbound**: it toggles a row into a bulk-action set and this
    // surface has none — a document is retried one at a time, because forcing
    // fifty attempts at a target that is refusing them is fifty more requests
    // and no more information.
    onOpen: (index) => {
      const row = shown[index] as { id?: string } | undefined;
      if (row?.id && segment !== "sin-enviar") go({ envio: row.id });
    },
    onEscape: () => {
      if (!search.envio) return false;
      go({ envio: undefined });
      return true;
    },
  });

  if (query.isError) {
    return (
      <>
        <Header />
        <Content>
          <RouteError
            title="No pudimos cargar los envíos a facturación."
            detail={
              query.error instanceof ApiError && query.error.status > 0
                ? query.error.message
                : "El servidor no respondió. Las ventas siguen registrándose " +
                  "normalmente; esta lista es la del envío al sistema de " +
                  "facturación."
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
      <Header />
      <FilterBar>
        <Segmented
          value={segment}
          segments={SEGMENTS}
          label="Qué envíos ver"
          onChange={(next) => go({ lista: next, page: 1, envio: undefined })}
        />
        <FilterChip
          label="Sede"
          value={locations.data?.find((one) => one.id === search.sede)?.name}
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todas" },
                ...(locations.data ?? []).map((one) => ({
                  value: one.id,
                  label: one.name,
                })),
              ]}
              value={search.sede ?? ""}
              onPick={(next) => {
                go({ sede: next || undefined, page: 1 });
                close();
              }}
            />
          )}
        </FilterChip>
      </FilterBar>
      {/* §B.10.1 · a re-fetch dims the rows **and** draws the 2px line under
          the filter bar. Dimming alone is indistinguishable from a table that
          has gone inert. */}
      <ProgressLine active={query.isFetching && !query.isPending} />
      <div className="flex min-h-0 flex-1">
        <TableContent>
          {segment === "sin-enviar" ? (
            <UnsentTable
              rows={shown as UnsentSaleRow[]}
              query={unsent}
              keys={keys}
              summary={summary.data}
              page={page}
              pageSize={pageSize}
              onPage={(next) => go({ page: next })}
              onPageSize={(next) => go({ pageSize: next, page: 1 })}
              onConfigure={() => settings.show("invoicing")}
            />
          ) : (
            <DocumentTable
              rows={shown as FiscalDocumentRow[]}
              query={documents}
              segment={segment}
              current={search.envio}
              keys={keys}
              page={page}
              pageSize={pageSize}
              filtered={!!search.sede}
              summary={summary.data}
              onOpen={(id) => go({ envio: id })}
              onClearFilters={() => go({ sede: undefined, page: 1 })}
              onConfigure={() => settings.show("invoicing")}
              onPage={(next) => go({ page: next })}
              onPageSize={(next) => go({ pageSize: next, page: 1 })}
            />
          )}
        </TableContent>
        {search.envio ? (
          <DocumentPanel
            documentId={search.envio}
            elevated={me.role !== "cashier"}
            onClose={() => go({ envio: undefined })}
          />
        ) : null}
      </div>
    </>
  );
}

function Header() {
  return <TopBar breadcrumb={[]} title={WORK_LIST} />;
}

/** The `Pendientes` and `Fallidos` lists. `Estado` is this surface's **one**
 *  badge column (§B.7.3), and it is the column the surface is about. */
function DocumentTable({
  rows,
  query,
  segment,
  current,
  keys,
  page,
  pageSize,
  filtered,
  summary,
  onOpen,
  onClearFilters,
  onConfigure,
  onPage,
  onPageSize,
}: {
  rows: FiscalDocumentRow[];
  query: ReturnType<typeof useFiscalDocuments>;
  segment: Segment;
  current?: string;
  keys: ReturnType<typeof useListKeys>;
  page: number;
  pageSize: number;
  filtered: boolean;
  summary: FiscalSummary | undefined;
  onOpen: (id: string) => void;
  onClearFilters: () => void;
  onConfigure: () => void;
  onPage: (next: number) => void;
  onPageSize: (next: number) => void;
}) {
  return (
    <DataTable<FiscalDocumentRow>
      rows={rows}
      rowId={(row) => row.id}
      density="standard"
      minWidth={1040}
      loading={query.isPending}
      refetching={query.isFetching && !query.isPending}
      skeletonRows={8}
      containerProps={keys.containerProps}
      rowProps={(row, index) => ({
        id: `fiscal-row-${index}`,
        cursor: keys.cursor === index,
        current: current === row.id,
        onClick: () => onOpen(row.id),
      })}
      empty={
        <Empty
          segment={segment}
          filtered={filtered}
          summary={summary}
          onClearFilters={onClearFilters}
          onConfigure={onConfigure}
        />
      }
      footer={
        <TableFooter
          page={page}
          pageSize={pageSize}
          rowCount={query.data?.row_count}
          loading={query.isPending}
          onPage={onPage}
          onPageSize={onPageSize}
        />
      }
      columns={[
        {
          key: "location",
          label: "Sede",
          width: "12%",
          truncate: true,
          render: (row) => row.location_name,
        },
        {
          key: "sale",
          label: "Venta",
          width: "14%",
          render: (row) => (
            <span className="tabular-nums text-ink">
              {row.sale_number}
              {row.type === "credit_note" ? (
                <span className="text-ink-label">
                  {" "}
                  {DOT} {DOCUMENT_TYPE.credit_note}
                </span>
              ) : null}
            </span>
          ),
        },
        {
          key: "target",
          label: "Destino",
          width: "12%",
          truncate: true,
          render: (row) => row.target_label,
        },
        {
          key: "created_at",
          label: "Creado",
          width: "12%",
          align: "right",
          render: (row) => since(row.created_at),
        },
        {
          key: "attempts",
          label: "Intentos",
          width: "8%",
          align: "right",
          numeric: true,
          render: (row) => count(row.attempts),
        },
        {
          key: "error",
          label: "Motivo",
          width: "24%",
          truncate: true,
          // 12px `#727272`, and **always something a person can act on**.
          render: (row) => (
            <span className="text-12 text-ink-label">
              {row.error || waitingLine(row)}
            </span>
          ),
        },
        {
          key: "status",
          label: "Estado",
          width: "18%",
          render: (row) => {
            const meaning = FISCAL_STATUS[row.status]!;
            return (
              <Badge family={meaning.family} dot={meaning.dot}>
                {meaning.label}
              </Badge>
            );
          },
        },
      ]}
    />
  );
}

/**
 * What a row with no `error` is waiting for. **Held and queued look identical
 * from a count**, so the column says which in words: a null `next_attempt_at`
 * is a document nothing will pick up until a person acts.
 */
function waitingLine(row: FiscalDocumentRow): string {
  if (row.status === "acknowledged")
    return "El sistema de facturación tiene el documento.";
  if (row.status === "sent")
    return "Entregado. Se vuelve a consultar hasta que el destino confirme.";
  if (!row.next_attempt_at)
    return "Retenido. No se reintenta solo: requiere una revisión.";
  return "En cola para envío.";
}

/**
 * §B.10.2 · **two kinds here that must not be conflated.**
 *
 * With a target configured and nothing pending: the deliberately-empty kind,
 * with what settled today as its body and no primary action. With **no target
 * configured**: the never-populated kind in the neutral family, whose action
 * points at the settings section. No count, no warning family, no badge (§8).
 */
export function Empty({
  segment,
  filtered,
  summary,
  onClearFilters,
  onConfigure,
}: {
  segment: Segment;
  //: The third list takes no sede filter -- `list_unsent_sales` has none -- so
  //: it never reaches the filtered kind and passes no way out of it.
  filtered: boolean;
  summary: FiscalSummary | undefined;
  onClearFilters?: () => void;
  onConfigure: () => void;
}) {
  // **A kind is a claim, so it waits for the answer.** Until the summary has
  // said whether anything is connected, the only honest empty state is none:
  // the deliberate copy asserts a system was connected, and rendering it first
  // would state the opposite of the truth on the instance that ships. An
  // errored summary never resolves, so it stays blank rather than lying.
  if (!summary) return null;
  // **Never populated**, in the neutral family, pointing at the section that
  // would fill it. No count, no warning family, no badge (§8).
  if (!summary.configured) {
    return (
      <EmptyState
        title="No hay ningún sistema de facturación conectado"
        body="Botica registra las ventas normalmente y no envía ningún documento. Conecte el sistema con el que la droguería factura para que las ventas lleguen a él."
        actionLabel="Configurar"
        onAction={onConfigure}
      />
    );
  }
  if (filtered && onClearFilters) {
    return (
      <EmptyState
        kind="filtered"
        title="Ningún envío coincide con estos filtros"
        body="La sede elegida no tiene envíos en esta lista."
        actionLabel="Quitar filtros"
        onAction={onClearFilters}
      />
    );
  }
  // **Deliberately empty**, and its body says what settled rather than
  // repeating the title. There is no action, because there is nothing for a
  // reader to do about it.
  if (segment === "sin-enviar") {
    return (
      <EmptyState
        kind="deliberate"
        title="Todas las ventas tienen su envío"
        body="Ninguna venta cerrada desde que se conectó el sistema de facturación quedó sin documento."
      />
    );
  }
  const other = segment === "fallidos" ? summary.unsent : summary.failed;
  return (
    <EmptyState
      kind="deliberate"
      title={
        segment === "fallidos"
          ? "No hay envíos fallidos"
          : "No hay envíos pendientes"
      }
      body={
        other
          ? segment === "fallidos"
            ? `${count(other)} documentos siguen en camino al sistema de facturación.`
            : `${count(other)} envíos fallidos esperan una corrección.`
          : "Todo lo que se cerró desde que se conectó el sistema de facturación llegó a él."
      }
    />
  );
}

/**
 * `Ventas sin enviar` — **sales, not documents**, so it carries no badge column
 * and each row states in words why no document exists.
 *
 * Reported and never repaired (S5, *Jobs*): a document recreated by a sweep
 * would hide the defect that produced the hole.
 */
function UnsentTable({
  rows,
  query,
  keys,
  summary,
  page,
  pageSize,
  onPage,
  onPageSize,
  onConfigure,
}: {
  rows: UnsentSaleRow[];
  query: ReturnType<typeof useUnsentSales>;
  keys: ReturnType<typeof useListKeys>;
  summary: FiscalSummary | undefined;
  page: number;
  pageSize: number;
  onPage: (next: number) => void;
  onPageSize: (next: number) => void;
  onConfigure: () => void;
}) {
  return (
    <DataTable<UnsentSaleRow>
      rows={rows}
      rowId={(row) => row.id}
      density="standard"
      minWidth={840}
      loading={query.isPending}
      refetching={query.isFetching && !query.isPending}
      skeletonRows={6}
      // §B.13.2 · `j` and `k` are every list surface's, not a per-screen
      // opt-in. This one opens no record panel -- an orphan is a sale, and the
      // sale's own detail is S4's -- but the cursor still has to render, or the
      // keyboard works on two of three lists and goes quiet on the third.
      containerProps={keys.containerProps}
      rowProps={(_row, index) => ({
        id: `fiscal-row-${index}`,
        cursor: keys.cursor === index,
      })}
      empty={
        <Empty
          segment="sin-enviar"
          filtered={false}
          summary={summary}
          onConfigure={onConfigure}
        />
      }
      footer={
        <TableFooter
          page={page}
          pageSize={pageSize}
          rowCount={query.data?.row_count}
          loading={query.isPending}
          onPage={onPage}
          onPageSize={onPageSize}
        />
      }
      columns={[
        {
          key: "location",
          label: "Sede",
          width: "16%",
          truncate: true,
          render: (row) => row.location_name,
        },
        {
          key: "number",
          label: "Venta",
          width: "16%",
          render: (row) => (
            <span className="tabular-nums text-ink">{row.sale_number}</span>
          ),
        },
        {
          key: "recorded_at",
          label: "Registrada",
          width: "16%",
          align: "right",
          render: (row) => since(row.recorded_at),
        },
        {
          key: "reason",
          label: "Motivo",
          width: "52%",
          truncate: true,
          render: (row) => (
            <span className="text-12 text-ink-label">{row.reason}</span>
          ),
        },
      ]}
    />
  );
}

/**
 * The record panel: the canonical payload as sent — **or as it renders now, for
 * a `failed` row**, which is what an administrator needs to see before pressing
 * anything — the target and its mapping version, the attempt trail, the
 * target's parsed response, the identifiers it returned, and `[Reintentar]`.
 */
function DocumentPanel({
  documentId,
  elevated,
  onClose,
}: {
  documentId: string;
  elevated: boolean;
  onClose: () => void;
}) {
  const query = useFiscalDocument(documentId);
  const retry = useRetryFiscalDocument();
  const toast = useToast();
  const row = query.data;
  const meaning = row ? FISCAL_STATUS[row.status] : undefined;

  return (
    <RecordPanel
      title={row ? row.document_key : "Envío"}
      open
      onClose={onClose}
      footer={
        elevated && row && row.status !== "acknowledged" ? (
          <Button
            size="sm"
            variant="primary"
            busy={retry.isPending}
            onClick={() =>
              retry.mutate(row.id, {
                onSuccess: () =>
                  toast(`Se reintentará el envío ${row.document_key}.`),
              })
            }
          >
            Reintentar
          </Button>
        ) : null
      }
    >
      {!row ? null : (
        <div className="flex flex-col gap-5">
          <dl className="flex flex-col gap-2 text-12">
            <Pair label="Estado" value={meaning ? meaning.label : row.status} />
            <Pair label="Tipo" value={DOCUMENT_TYPE[row.type] ?? row.type} />
            <Pair label="Sede" value={row.location_name} />
            <Pair label="Venta" value={row.sale_number} />
            <Pair label="Destino" value={row.target_label} />
            <Pair label="Mapeo" value={row.mapping_version || "—"} />
            <Pair label="Intentos" value={count(row.attempts)} />
            <Pair label="Creado" value={since(row.created_at)} />
            {row.acknowledged_at ? (
              <Pair label="Confirmación" value={since(row.acknowledged_at)} />
            ) : null}
            {/* Where the target returned nothing, the row still records that
                the handoff succeeded — null is normal and is not a failure. */}
            {row.external_number ? (
              <Pair label="Número del sistema" value={row.external_number} />
            ) : null}
            {row.cude ? <Pair label="CUDE" value={row.cude} /> : null}
          </dl>

          {row.error ? (
            <RegionError
              title="Este envío no llegó al sistema de facturación."
              detail={row.error}
            />
          ) : null}

          <section>
            <p className="mb-2 font-mono text-10 uppercase tracking-eyebrow text-ink-note">
              Documento
              {row.payload_is_current_render ? " · como se enviaría hoy" : ""}
            </p>
            <pre className="surface-scroll max-h-[380px] select-all overflow-auto rounded-card bg-chrome p-3 font-mono text-11 leading-[16px] text-ink-body">
              {JSON.stringify(row.payload, null, 2)}
            </pre>
          </section>

          {row.pdf_url ? (
            <a
              className="text-12 text-brand underline-offset-2 hover:underline"
              href={row.pdf_url}
              target="_blank"
              rel="noreferrer"
            >
              Ver el documento del sistema de facturación
            </a>
          ) : null}
        </div>
      )}
    </RecordPanel>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="shrink-0 text-12 text-ink-label">{label}</dt>
      <dd className="min-w-0 truncate text-right text-14 text-ink">{value}</dd>
    </div>
  );
}
