import type { HTMLAttributes, ReactNode } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
} from "lucide-react";
import { cn } from "./cn";
import { Checkbox } from "./field";
import { Select } from "./select";
import { SkeletonBar, SkeletonRows } from "./states";
import { range as formatRange, count as formatCount } from "./format";

/**
 * §B.4.1 · four density modes. Density is a property of the surface, decided in
 * its spec. **Do not build a density toggle.** Cell padding-x stays 22px in the
 * first three modes because it is the handoff's own inset and a narrower cell
 * does not buy a row.
 */
export type Density = "compact" | "panel" | "standard" | "counter";

export const ROW_HEIGHT: Record<Density, string> = {
  compact: "h-10",
  panel: "h-11",
  standard: "h-12",
  counter: "h-row-counter",
};

const CELL_PAD: Record<Density, string> = {
  compact: "px-[22px]",
  panel: "px-[22px]",
  standard: "px-[22px]",
  counter: "px-5",
};

/** §A.17 · the frame: 16px, an 8% border, the plane shadow, clipping its own body. */
export function TableFrame({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-panel border border-edge-soft",
        "bg-surface shadow-plane",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function TableScroll({ children }: { children: ReactNode }) {
  return (
    <div className="surface-scroll min-h-0 flex-1 overflow-auto">
      {children}
    </div>
  );
}

/**
 * §B.4.4 · widths are percentages under `table-layout: fixed`. **Never write a
 * column width as a `calc()` containing a percentage** -- Chrome does not
 * resolve one and divides the table equally instead, so the shares read as
 * deliberate and are inert.
 */
export function Table({
  children,
  className,
  minWidth,
}: {
  children: ReactNode;
  className?: string;
  minWidth?: number;
}) {
  return (
    <table
      style={minWidth ? { minWidth } : undefined}
      className={cn("w-full table-fixed border-collapse text-left", className)}
    >
      {children}
    </table>
  );
}

/** §B.4.2 · 40px, sticky, an **opaque** L0 fill -- a translucent blurred header
 *  smears row text as it scrolls beneath it. */
export function Th({
  children,
  align = "left",
  width,
  sortable,
  sorted,
  onSort,
  sticky,
  className,
}: {
  children: ReactNode;
  align?: "left" | "right";
  width?: string;
  sortable?: boolean;
  sorted?: "asc" | "desc" | false;
  onSort?: () => void;
  /** Pinned left as the frame scrolls sideways, above the body's own sticky
   *  cells so the two never cross. */
  sticky?: boolean;
  className?: string;
}) {
  const label = (
    <span
      className={cn(
        "inline-flex items-center gap-1",
        align === "right" && "flex-row-reverse",
      )}
    >
      {children}
      {/* A chevron on the active sort column only: a hover-revealed one
          reflows 12px on every column the pointer crosses. */}
      {sorted === "asc" ? (
        <ChevronUp
          aria-hidden
          strokeWidth={2}
          className="size-3 text-ink-soft"
        />
      ) : sorted === "desc" ? (
        <ChevronDown
          aria-hidden
          strokeWidth={2}
          className="size-3 text-ink-soft"
        />
      ) : null}
    </span>
  );
  return (
    <th
      scope="col"
      style={width ? { width } : undefined}
      aria-sort={
        sorted === "asc"
          ? "ascending"
          : sorted === "desc"
            ? "descending"
            : undefined
      }
      className={cn(
        "sticky top-0 z-10 h-10 whitespace-nowrap border-b border-edge bg-chrome",
        sticky && "left-0 z-20",
        "px-[22px] font-mono text-10 uppercase tracking-eyebrow text-ink-note",
        align === "right" && "text-right",
        className,
      )}
    >
      {sortable ? (
        <button
          type="button"
          onClick={onSort}
          className={cn(
            "-mx-[22px] h-10 w-[calc(100%+44px)] px-[22px] text-left uppercase",
            "transition-colors duration-140 ease-out hover:text-ink",
            align === "right" && "text-right",
          )}
        >
          {label}
        </button>
      ) : (
        label
      )}
    </th>
  );
}

/**
 * §B.4.3 · two fills and two markers, and each says one thing. The fill is the
 * weight of attention: `#f4f4f4` means the pointer or the cursor is here,
 * `#e8e8e8` means this row is committed to something. The marker says which
 * pointer: `#909090` is the keyboard cursor, `#171717` is the open record, and
 * the ink marker wins over the grey one.
 *
 * The keyboard cursor, DOM focus and selection are three separate states. Do
 * not collapse them into one boolean.
 */
export function Tr({
  id,
  density = "standard",
  cursor,
  current,
  checked,
  onClick,
  children,
}: {
  id?: string;
  density?: Density;
  /** The `j`/`k` cursor. */
  cursor?: boolean;
  /** Its record panel is open -- the drawn state. */
  current?: boolean;
  /** In a bulk-action set. */
  checked?: boolean;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <tr
      id={id}
      role="row"
      aria-selected={checked}
      aria-current={current ? "true" : undefined}
      onClick={onClick}
      className={cn(
        "group border-b border-hairline last:border-b-0 scroll-mt-24",
        "transition-[background-color] duration-140 ease-out",
        ROW_HEIGHT[density],
        onClick && "cursor-pointer",
        // **The rest fill is opaque, not absent.** A transparent row is
        // indistinguishable from an L0 one until a column is pinned left --
        // then `bg-inherit` on the sticky cell inherits *nothing* and the
        // columns scrolling under it show straight through the product name.
        // The fill is the frame's own colour, so this changes how every table
        // looks in exactly no way, and makes the pinned column occlude.
        current || checked
          ? "bg-active"
          : cursor
            ? "bg-hover-row"
            : "bg-surface hover:bg-hover-row",
        current
          ? "shadow-[inset_2px_0_0_var(--color-ink)]"
          : cursor
            ? "shadow-[inset_2px_0_0_var(--color-ink-soft)]"
            : "",
      )}
    >
      {children}
    </tr>
  );
}

/** §A.17 · first column ink, the rest body; numeric right and tabular. */
export function Td({
  children,
  density = "standard",
  align = "left",
  numeric,
  truncate,
  className,
  onClick,
}: {
  children: ReactNode;
  density?: Density;
  align?: "left" | "right";
  numeric?: boolean;
  truncate?: boolean;
  className?: string;
  onClick?: (event: React.MouseEvent<HTMLTableCellElement>) => void;
}) {
  return (
    <td
      onClick={onClick}
      className={cn(
        "align-middle text-14 text-ink-body",
        CELL_PAD[density],
        align === "right" && "text-right",
        numeric && "whitespace-nowrap tabular-nums",
        truncate && "max-w-0 truncate",
        className,
      )}
    >
      {children}
    </td>
  );
}

export interface Column<Row> {
  key: string;
  label: ReactNode;
  width?: string;
  align?: "left" | "right";
  sortable?: boolean;
  numeric?: boolean;
  truncate?: boolean;
  /** Pinned left in **both** halves of the table. `className` styles the body
   *  cell; this is what carries the same promise to the header. */
  sticky?: boolean;
  className?: string;
  render: (row: Row) => ReactNode;
}

export interface Selection<Row> {
  checkedIds: ReadonlySet<string>;
  onToggle: (row: Row, checked: boolean) => void;
  onToggleAll: (rows: Row[], checked: boolean) => void;
  label: (row: Row) => string;
  allLabel?: string;
}

export function DataTable<Row>({
  rows,
  columns,
  rowId,
  sort,
  order,
  onSort,
  selection,
  loading,
  refetching,
  skeletonWidths,
  skeletonRows = 10,
  empty,
  footer,
  density = "standard",
  minWidth,
  containerProps,
  rowProps,
}: {
  rows: Row[];
  columns: Column<Row>[];
  rowId: (row: Row) => string;
  sort?: string;
  order?: "asc" | "desc";
  onSort?: (key: string) => void;
  selection?: Selection<Row>;
  loading?: boolean;
  refetching?: boolean;
  skeletonWidths?: string[];
  skeletonRows?: number;
  empty?: ReactNode;
  footer?: ReactNode;
  density?: Density;
  /** §B.4.4 · below this the frame scrolls, rather than the headers clipping. */
  minWidth?: number;
  containerProps?: HTMLAttributes<HTMLDivElement>;
  rowProps?: (
    row: Row,
    index: number,
  ) => {
    id?: string;
    cursor?: boolean;
    current?: boolean;
    onClick?: () => void;
  };
}) {
  const checkedOnPage = selection
    ? rows.filter((row) => selection.checkedIds.has(rowId(row))).length
    : 0;

  return (
    <TableFrame>
      <TableScroll>
        <div {...containerProps}>
          <Table minWidth={minWidth}>
            <thead>
              <tr>
                {selection ? (
                  <Th width="44px" className="px-0">
                    <Checkbox
                      aria-label={
                        selection.allLabel ?? "Seleccionar todas las filas"
                      }
                      checked={rows.length > 0 && checkedOnPage === rows.length}
                      indeterminate={
                        checkedOnPage > 0 && checkedOnPage < rows.length
                      }
                      onChange={(next) => selection.onToggleAll(rows, next)}
                    />
                  </Th>
                ) : null}
                {columns.map((column) => (
                  <Th
                    key={column.key}
                    width={column.width}
                    align={column.align}
                    sortable={column.sortable}
                    sorted={column.key === sort && order ? order : false}
                    onSort={() => onSort?.(column.key)}
                    // **A sticky column is sticky in both halves or in
                    // neither.** Pinning only the body cells slides the header
                    // off the column it labels the moment the frame scrolls
                    // sideways, which is worse than not pinning at all: the
                    // reader is left with a pinned column of names under
                    // somebody else's heading. `Th` supplies its own opaque L0
                    // fill and its own z-order, so the flag is what travels
                    // rather than the class.
                    sticky={column.sticky}
                  >
                    {column.label}
                  </Th>
                ))}
              </tr>
            </thead>
            <tbody
              className={cn(
                refetching && !loading && "pointer-events-none opacity-60",
              )}
            >
              {loading ? (
                <SkeletonRows
                  rows={skeletonRows}
                  rowHeight={ROW_HEIGHT[density]}
                  padding={CELL_PAD[density]}
                  columns={skeletonWidths ?? columns.map(() => "60%")}
                />
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length + (selection ? 1 : 0)}>
                    {empty}
                  </td>
                </tr>
              ) : (
                rows.map((row, index) => {
                  const id = rowId(row);
                  const interaction = rowProps?.(row, index);
                  return (
                    <Tr
                      key={id}
                      density={density}
                      checked={selection?.checkedIds.has(id)}
                      {...interaction}
                    >
                      {selection ? (
                        <Td
                          density={density}
                          className="px-0"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <Checkbox
                            aria-label={selection.label(row)}
                            checked={selection.checkedIds.has(id)}
                            onChange={(next) => selection.onToggle(row, next)}
                          />
                        </Td>
                      ) : null}
                      {columns.map((column) => (
                        <Td
                          key={column.key}
                          density={density}
                          align={column.align}
                          numeric={column.numeric}
                          truncate={column.truncate}
                          className={column.className}
                        >
                          {column.render(row)}
                        </Td>
                      ))}
                    </Tr>
                  );
                })
              )}
            </tbody>
          </Table>
        </div>
      </TableScroll>
      {footer}
    </TableFrame>
  );
}

export const PAGE_SIZES = [25, 50, 100] as const;

/**
 * §A.17 + §B.4.5 · the 48px footer. Until `row_count` arrives the range is a
 * skeleton bar at its resting width and the page group is not rendered --
 * never `… de muchos`, never a guessed page count.
 */
export function TableFooter({
  page,
  pageSize,
  rowCount,
  onPage,
  onPageSize,
  loading,
  annotation,
}: {
  page: number;
  pageSize: number;
  rowCount: number | undefined;
  onPage: (next: number) => void;
  onPageSize: (next: number) => void;
  loading?: boolean;
  annotation?: string;
}) {
  const known = typeof rowCount === "number" && !loading;
  const lastPage = known ? Math.max(1, Math.ceil(rowCount / pageSize)) : 1;
  const first = known && rowCount === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = known ? Math.min(page * pageSize, rowCount) : 0;

  return (
    <div className="flex h-12 shrink-0 items-center justify-between gap-4 border-t border-hairline bg-chrome px-[22px]">
      {known ? (
        <span className="flex items-baseline gap-2">
          <span className="text-11 tabular-nums text-ink-note">
            {formatRange(first, last, rowCount)}
          </span>
          {annotation ? (
            <span className="text-11 text-ink-note">{annotation}</span>
          ) : null}
        </span>
      ) : (
        <SkeletonBar className="h-2.5 w-28" />
      )}

      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1.5 text-11 text-ink-note">
          Filas
          <Select
            size="sm"
            containerClassName="w-16"
            className="tabular-nums"
            value={pageSize}
            onValueChange={(value) => onPageSize(Number(value))}
            options={PAGE_SIZES.map((rows) => ({
              value: rows,
              label: String(rows),
            }))}
          />
        </label>

        <PageArrow
          direction="previous"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        />
        {known ? (
          <PageNumbers page={page} lastPage={lastPage} onPage={onPage} />
        ) : null}
        <PageArrow
          direction="next"
          disabled={!known || page >= lastPage}
          onClick={() => onPage(page + 1)}
        />
      </div>
    </div>
  );
}

/** §A.15.4 · 28×28, a 14px icon at `stroke-width:2`; disabled goes to
 *  `#c8c8c8`, which is the handoff's own treatment for a ghost control. */
function PageArrow({
  direction,
  disabled,
  onClick,
}: {
  direction: "previous" | "next";
  disabled: boolean;
  onClick: () => void;
}) {
  const Icon = direction === "previous" ? ChevronLeft : ChevronRight;
  return (
    <button
      type="button"
      aria-label={
        direction === "previous" ? "Página anterior" : "Página siguiente"
      }
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex size-7 items-center justify-center rounded-control",
        "transition-colors duration-140 ease-out",
        disabled
          ? "pointer-events-none text-ink-disabled"
          : "text-ink-body hover:bg-hover-nav hover:text-ink",
      )}
    >
      <Icon aria-hidden strokeWidth={2} className="size-3.5" />
    </button>
  );
}

const cell = (digits: number) => `max(32px, ${digits}ch + 16px)`;
const GAP = "calc(1ch + 12px)";

/**
 * §B.4.5 · reserve the group's width in `ch`, computed from the widest
 * arrangement `rowCount` permits -- the numerals are tabular, so one digit is
 * exactly `1ch` -- rather than measuring the rendered group, which is a layout
 * read on every paint and wrong the moment the viewport changes.
 */
function reservedWidth(lastPage: number) {
  const cells: string[] = [];
  if (lastPage <= 7) {
    for (let index = 1; index <= lastPage; index += 1) {
      cells.push(cell(String(index).length));
    }
  } else {
    const widest = String(lastPage).length;
    cells.push(
      cell(1),
      GAP,
      cell(widest),
      cell(widest),
      cell(widest),
      GAP,
      cell(widest),
    );
  }
  return `calc(${cells.join(" + ")} + ${(cells.length - 1) * 4}px)`;
}

function PageNumbers({
  page,
  lastPage,
  onPage,
}: {
  page: number;
  lastPage: number;
  onPage: (next: number) => void;
}) {
  const cells: (number | "gap")[] = [];
  for (let index = 1; index <= lastPage; index += 1) {
    if (index === 1 || index === lastPage || Math.abs(index - page) <= 1) {
      cells.push(index);
    } else if (cells[cells.length - 1] !== "gap") {
      cells.push("gap");
    }
  }
  return (
    <div
      style={{ minWidth: reservedWidth(lastPage) }}
      className="flex items-center justify-center gap-1"
    >
      {cells.map((value, index) =>
        value === "gap" ? (
          <span
            key={`gap-${index}`}
            aria-hidden
            className="px-1.5 text-11 text-ink-disabled"
          >
            …
          </span>
        ) : (
          <button
            key={value}
            type="button"
            aria-current={value === page ? "page" : undefined}
            onClick={() => onPage(value)}
            className={cn(
              "h-7 min-w-8 rounded-control px-2 text-12 tabular-nums",
              "transition-colors duration-140 ease-out",
              value === page
                ? "bg-active font-medium text-ink"
                : "text-ink-body hover:bg-hover-nav hover:text-ink",
            )}
          >
            {value}
          </button>
        ),
      )}
    </div>
  );
}

/**
 * §B.4.5 · the bulk-action bar. A 48px L0 strip pinned inside the table frame
 * above the footer, with at most one primary.
 */
export function BulkBar({
  count,
  noun,
  onClear,
  actions,
}: {
  count: number;
  noun: string;
  onClear: () => void;
  actions?: ReactNode;
}) {
  return (
    <div
      role="group"
      aria-label="Selección"
      className="flex h-12 shrink-0 items-center gap-4 border-t border-hairline bg-chrome px-[22px]"
    >
      <span className="text-12 tabular-nums text-ink">
        {formatCount(count)} {noun}
      </span>
      <button
        type="button"
        onClick={onClear}
        className="rounded-control px-2 py-1 text-12 text-ink-body transition-colors duration-140 ease-out hover:text-ink"
      >
        Quitar selección
      </button>
      <span className="ml-auto flex items-center gap-2">{actions}</span>
    </div>
  );
}
