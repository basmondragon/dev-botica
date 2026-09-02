import { useMemo, useState } from "react";
import { useItems, type ItemRow } from "@/api/catalog";
import { Combobox } from "@/ui/combobox";
import { money } from "@/ui/format";

/**
 * §B.5.4 · **the catalog combobox**, built once here and used by S3's
 * receiving, S4's product lookup, S6's order lines and S7's price grid.
 *
 * A droguería's catalog is thousands of rows and a select is not a search, so
 * the term goes to the server -- the same `q` the grid uses, matching name,
 * laboratorio, barcode and registro INVIMA. Only twenty rows come back: a
 * picker is for choosing one thing, and a scroll of four thousand is a grid.
 *
 * **A deactivated reference does not appear.** `active` is left at its default,
 * which is what keeps a till from offering a product the network stopped
 * selling.
 */
export function ItemCombobox({
  id,
  value,
  placeholder = "Busque un producto",
  ariaLabel,
  type,
  invalid,
  onChange,
}: {
  id?: string;
  value: string;
  placeholder?: string;
  ariaLabel?: string;
  /** Narrow to products or to services where a surface only takes one. */
  type?: ItemRow["type"];
  invalid?: boolean;
  onChange: (id: string, item: ItemRow | undefined) => void;
}) {
  const [term, setTerm] = useState("");
  // The chosen row has to survive a search that no longer returns it, or the
  // trigger goes blank the moment somebody types the next thing.
  const [chosen, setChosen] = useState<string | undefined>();
  const results = useItems({
    q: term || undefined,
    type,
    active: "true",
    page: 1,
    page_size: 25,
    order: "asc",
  });
  const rows = useMemo(() => results.data?.rows ?? [], [results.data]);
  const options = useMemo(
    () =>
      rows.map((row) => ({
        value: row.id,
        label: label(row),
        hint: row.price === null ? undefined : money(Number(row.price)),
      })),
    [rows],
  );

  return (
    <Combobox
      id={id}
      ariaLabel={ariaLabel}
      value={value}
      label={value ? chosen : undefined}
      options={options}
      placeholder={placeholder}
      searchPlaceholder="Nombre, laboratorio, código o registro"
      emptyLabel="Ningún producto coincide con esa búsqueda."
      loading={results.isFetching}
      invalid={invalid}
      onSearch={setTerm}
      onChange={(next) => {
        const row = rows.find((one) => one.id === next);
        setChosen(row ? label(row) : undefined);
        onChange(next, row);
      }}
    />
  );
}

/** What a picker shows for a row: the name, and its presentación where two rows
 *  would otherwise read the same. */
function label(row: ItemRow) {
  return row.presentation ? `${row.name} · ${row.presentation}` : row.name;
}
