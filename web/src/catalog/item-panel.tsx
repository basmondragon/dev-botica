import { useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { ApiError } from "@/api/client";
import {
  useCategories,
  useCreateItem,
  useDeleteSupplierItem,
  useItem,
  useManufacturers,
  useSaveSupplierItem,
  useSetPrice,
  useSuppliers,
  useUpdateItem,
  useWithdrawPrice,
  type ItemDetail,
  type ItemType,
  type PriceRow,
  type VatClass,
} from "@/api/catalog";
import type { Location, Me } from "@/api/queries";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Combobox } from "@/ui/combobox";
import { Checkbox, Field, Input, Textarea } from "@/ui/field";
import { dayMonth, money } from "@/ui/format";
import { RecordPanel } from "@/ui/panel";
import { Select } from "@/ui/select";
import { Badge, INVIMA_STATUS } from "@/ui/status";
import { BAR, RegionError, SkeletonBar } from "@/ui/states";
import { useToast } from "@/ui/toast";
import { INVIMA_LABEL, UNITS, VAT_CLASS, enumOptions } from "./vocabulary";

/**
 * §B.8.5 · the item editor, in the 440px record panel.
 *
 * Sections, separated by a hairline: **Identidad · Presentación · Registro
 * sanitario · Manejo · Impuesto · Precio · Códigos de barras · Proveedores**,
 * minus everything a service removes and plus **Costo del servicio** for one.
 *
 * An item with `tracks_stock = false` leaves eleven columns without meaning.
 * The editor does not render them, so the meaninglessness never becomes stale
 * data somebody later trusts.
 */
export function ItemPanel({
  itemId,
  creating,
  me,
  locations,
  onClose,
  onCreated,
}: {
  itemId?: string;
  creating: boolean;
  me: Me;
  locations: Location[];
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const query = useItem(creating ? undefined : itemId);
  const item = creating ? undefined : query.data;
  const elevated = me.role !== "cashier";

  // The loaded panel renders its own `RecordPanel`, because §B.8.5 puts the
  // footer **below** the scrolling body on the panel's own surface -- it must
  // never scroll away from what it acts on -- and only the form knows whether
  // there is one to draw.
  if (!creating && (query.isPending || query.isError)) {
    return (
      <RecordPanel title={item?.name ?? "Producto"} open onClose={onClose}>
        {query.isPending ? (
          <PanelSkeleton />
        ) : (
          <RegionError
            title="No pudimos cargar este producto."
            detail={
              query.error instanceof ApiError
                ? query.error.message
                : "El servidor no respondió."
            }
            requestId={
              query.error instanceof ApiError
                ? query.error.requestId
                : undefined
            }
            onRetry={() => void query.refetch()}
          />
        )}
      </RecordPanel>
    );
  }

  return (
    <ItemForm
      key={item?.id ?? "new"}
      item={item}
      elevated={elevated}
      locations={locations}
      onClose={onClose}
      onCreated={onCreated}
    />
  );
}

/** §B.10.1 · the real field stack at the real control heights. */
function PanelSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      {[0, 1, 2, 3].map((section) => (
        <div key={section} className="flex flex-col gap-4">
          <SkeletonBar className="h-3 w-24" />
          <SkeletonBar className={BAR.control} />
          <SkeletonBar className={BAR.control} />
        </div>
      ))}
    </div>
  );
}

interface Draft {
  type: ItemType;
  name: string;
  description: string;
  manufacturer_id: string;
  category_id: string;
  presentation: string;
  active_ingredient: string;
  strength: string;
  invima_registration: string;
  invima_expires_at: string;
  invima_status: ItemDetail["invima_status"];
  requires_prescription: boolean;
  controlled: boolean;
  cold_chain: boolean;
  unit: string;
  splittable: boolean;
  units_per_pack: string;
  vat_class: VatClass | "";
  tracks_lots: boolean;
  tracks_expiry: boolean;
  active: boolean;
  service_cost: string;
  external_code: string;
  /** Only on a new product: the figure the opening price is posted with once
   *  the item has an id. An existing item prices through the Precio section. */
  price: string;
}

function draftFrom(item: ItemDetail | undefined): Draft {
  return {
    type: item?.type ?? "product",
    name: item?.name ?? "",
    description: item?.description ?? "",
    manufacturer_id: item?.manufacturer_id ?? "",
    category_id: item?.category_id ?? "",
    presentation: item?.presentation ?? "",
    active_ingredient: item?.active_ingredient ?? "",
    strength: item?.strength ?? "",
    invima_registration: item?.invima_registration ?? "",
    invima_expires_at: item?.invima_expires_at ?? "",
    invima_status: item?.invima_status ?? "not_applicable",
    requires_prescription: item?.requires_prescription ?? false,
    controlled: item?.controlled ?? false,
    cold_chain: item?.cold_chain ?? false,
    unit: item?.unit ?? "",
    splittable: item?.splittable ?? false,
    units_per_pack: String(item?.units_per_pack ?? 1),
    // §B.5.7 · **nothing preselected**. Defaulting to `excluded` silently
    // under-charges IVA on every cosmetic, drink and device.
    vat_class: item?.vat_class ?? "",
    tracks_lots: item?.tracks_lots ?? true,
    tracks_expiry: item?.tracks_expiry ?? true,
    active: item?.active ?? true,
    service_cost: item?.service_cost ?? "",
    external_code: item?.external_code ?? "",
    price: "",
  };
}

function ItemForm({
  item,
  elevated,
  locations,
  onClose,
  onCreated,
}: {
  item: ItemDetail | undefined;
  elevated: boolean;
  locations: Location[];
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(item));
  const [codes, setCodes] = useState(
    () =>
      item?.barcodes.map((one) => ({
        code: one.code,
        is_primary: one.is_primary,
      })) ?? [],
  );
  const [submitted, setSubmitted] = useState(false);
  const [labSearch, setLabSearch] = useState("");
  const create = useCreateItem();
  const update = useUpdateItem();
  const setPrice = useSetPrice();
  const toast = useToast();

  const manufacturers = useManufacturers(elevated);
  const categories = useCategories(elevated);

  const service = draft.type === "service";
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  const nameError =
    submitted && !draft.name.trim() ? "Escriba el nombre." : undefined;
  const unitError =
    submitted && !draft.unit.trim() ? "Escriba la unidad base." : undefined;
  const vatError =
    submitted && !draft.vat_class ? "Elija la clase de IVA." : undefined;
  const invalid = !!nameError || !!unitError || !!vatError;

  const labOptions = useMemo(
    () =>
      (manufacturers.data ?? [])
        .filter((one) =>
          labSearch
            ? one.name
                .toLocaleLowerCase()
                .includes(labSearch.toLocaleLowerCase())
            : true,
        )
        .map((one) => ({ value: one.id, label: one.name })),
    [manufacturers.data, labSearch],
  );

  const categoryOptions = useMemo(
    () =>
      (categories.data ?? []).map((one) => ({
        value: one.id,
        label: one.parent_name ? `${one.parent_name} · ${one.name}` : one.name,
      })),
    [categories.data],
  );

  function save() {
    setSubmitted(true);
    if (!draft.name.trim() || !draft.unit.trim() || !draft.vat_class) return;
    const body = {
      type: draft.type,
      name: draft.name.trim(),
      description: draft.description,
      manufacturer_id: draft.manufacturer_id || null,
      category_id: draft.category_id || null,
      clear_manufacturer: !draft.manufacturer_id,
      clear_category: !draft.category_id,
      presentation: draft.presentation,
      active_ingredient: draft.active_ingredient,
      strength: draft.strength,
      invima_registration: draft.invima_registration,
      invima_expires_at: draft.invima_expires_at || null,
      clear_invima_expires_at: !draft.invima_expires_at,
      invima_status: draft.invima_status,
      requires_prescription: draft.requires_prescription,
      controlled: draft.controlled,
      cold_chain: draft.cold_chain,
      unit: draft.unit.trim(),
      splittable: draft.splittable,
      units_per_pack: Number(draft.units_per_pack) || 1,
      vat_class: draft.vat_class as VatClass,
      tracks_lots: draft.tracks_lots,
      tracks_expiry: draft.tracks_expiry,
      active: draft.active,
      service_cost: draft.service_cost || null,
      external_code: draft.external_code,
      barcodes: codes,
    };
    const say = (error: unknown, fallback: string) =>
      toast(error instanceof ApiError ? error.message : fallback);

    if (item) {
      update.mutate(
        { id: item.id, body },
        {
          onSuccess: () => toast("Se guardaron los cambios."),
          onError: (error) => say(error, "No pudimos guardar el producto."),
        },
      );
    } else {
      create.mutate(
        { ...body, tracks_stock: draft.type === "product", custom: {} },
        {
          onSuccess: (created) => {
            const opening = draft.price.trim();
            if (!opening) {
              toast("Se creó el producto.");
              onCreated(created.id);
              return;
            }
            // A11 · the opening price goes to **the one endpoint that writes a
            // price**, as a second call. One press for the person, two calls on
            // the wire, and no second write path on the server.
            setPrice.mutate(
              { id: created.id, body: { price: opening } },
              {
                onSuccess: () => {
                  toast("Se creó el producto con su precio.");
                  onCreated(created.id);
                },
                onError: (error) => {
                  toast(
                    error instanceof ApiError
                      ? `Se creó el producto, pero no su precio: ${error.message}`
                      : "Se creó el producto, pero no pudimos fijar su precio.",
                  );
                  onCreated(created.id);
                },
              },
            );
          },
          onError: (error) => say(error, "No pudimos crear el producto."),
        },
      );
    }
  }

  const busy = create.isPending || update.isPending || setPrice.isPending;

  return (
    <RecordPanel
      title={item ? item.name : "Nuevo producto"}
      open
      onClose={onClose}
      footer={
        /* §B.8.3 · a `cashier` sees the panel read-only with **no footer**. The
           controls above are the same ones; what a counter person cannot do is
           save, and the action is absent rather than rendered disabled. */
        elevated ? (
          <div className="flex items-center justify-end gap-2">
            <Button variant="secondary" onClick={onClose} disabled={busy}>
              Cancelar
            </Button>
            <Button
              variant="primary"
              busy={busy}
              busyLabel="Guardando…"
              onClick={save}
              aria-invalid={invalid || undefined}
            >
              Guardar
            </Button>
          </div>
        ) : null
      }
    >
      {/* §B.8.5 · a `cashier` reaches this panel **read-only**. One disabled
          fieldset rather than a prop threaded through thirty controls: the rule
          is about the whole record, and a control that looks editable and
          refuses on save is worse than one that says so. */}
      <fieldset
        disabled={!elevated}
        className="m-0 flex min-w-0 flex-col border-0 p-0"
      >
        <Section title="Identidad" first>
          <Field label="Tipo" htmlFor="item-type">
            <Select
              id="item-type"
              value={draft.type}
              options={[
                { value: "product", label: "Producto" },
                { value: "service", label: "Servicio" },
              ]}
              disabled={!!item}
              onValueChange={(next) => set("type", next as ItemType)}
            />
          </Field>
          <Field label="Nombre" htmlFor="item-name" error={nameError} required>
            <Input
              id="item-name"
              value={draft.name}
              invalid={!!nameError}
              onChange={(event) => set("name", event.currentTarget.value)}
            />
          </Field>
          {!service ? (
            <Field label="Laboratorio" htmlFor="item-lab" optional>
              <Combobox
                id="item-lab"
                value={draft.manufacturer_id}
                options={labOptions}
                placeholder="Sin laboratorio"
                loading={manufacturers.isPending}
                onSearch={setLabSearch}
                onChange={(next) => set("manufacturer_id", next)}
              />
            </Field>
          ) : null}
          <Field label="Categoría" htmlFor="item-category" optional>
            <Combobox
              id="item-category"
              value={draft.category_id}
              options={categoryOptions}
              placeholder="Sin categoría"
              loading={categories.isPending}
              onChange={(next) => set("category_id", next)}
            />
          </Field>
          <Field label="Descripción" htmlFor="item-description" optional>
            <Textarea
              id="item-description"
              value={draft.description}
              onChange={(event) =>
                set("description", event.currentTarget.value)
              }
            />
          </Field>
          {/* The service table removes the **Registro sanitario** section
              entirely and keeps `invima_status` read-only as the badge. It sits
              here rather than under a heading of its own, because a heading
              over one inert badge is the section the spec says not to render. */}
          {service ? (
            <Field label="Registro sanitario" htmlFor="item-invima-badge">
              <span id="item-invima-badge">
                <Badge
                  family={INVIMA_STATUS.not_applicable!.family}
                  dot={INVIMA_STATUS.not_applicable!.dot}
                >
                  {INVIMA_STATUS.not_applicable!.label}
                </Badge>
              </span>
            </Field>
          ) : null}
        </Section>

        {!service ? (
          <Section title="Presentación">
            <Field label="Presentación" htmlFor="item-presentation" optional>
              <Input
                id="item-presentation"
                value={draft.presentation}
                placeholder="caja × 30 tabletas"
                onChange={(event) =>
                  set("presentation", event.currentTarget.value)
                }
              />
            </Field>
            <Field label="Principio activo" htmlFor="item-ingredient" optional>
              <Input
                id="item-ingredient"
                value={draft.active_ingredient}
                onChange={(event) =>
                  set("active_ingredient", event.currentTarget.value)
                }
              />
            </Field>
            <Field label="Concentración" htmlFor="item-strength" optional>
              <Input
                id="item-strength"
                value={draft.strength}
                placeholder="500 mg"
                onChange={(event) => set("strength", event.currentTarget.value)}
              />
            </Field>
          </Section>
        ) : null}

        {!service ? (
          <Section title="Registro sanitario">
            <Field label="Registro INVIMA" htmlFor="item-invima" optional>
              <Input
                id="item-invima"
                value={draft.invima_registration}
                placeholder="INVIMA 2019M-0012345"
                onChange={(event) =>
                  set("invima_registration", event.currentTarget.value)
                }
              />
            </Field>
            <Field label="Vence" htmlFor="item-invima-date" optional>
              <Input
                id="item-invima-date"
                type="date"
                value={draft.invima_expires_at}
                onChange={(event) =>
                  set("invima_expires_at", event.currentTarget.value)
                }
              />
            </Field>
            <Field label="Estado del registro" htmlFor="item-invima-status">
              <Select
                id="item-invima-status"
                value={draft.invima_status}
                options={enumOptions(INVIMA_LABEL)}
                onValueChange={(next) =>
                  set("invima_status", next as Draft["invima_status"])
                }
              />
            </Field>
          </Section>
        ) : null}

        <Section title="Unidad base">
          <Field
            label="Unidad"
            htmlFor="item-unit"
            error={unitError}
            required
            help={
              unitError
                ? undefined
                : "Toda cantidad en Botica se cuenta en esta unidad."
            }
          >
            <Input
              id="item-unit"
              list="item-unit-options"
              value={draft.unit}
              invalid={!!unitError}
              onChange={(event) => set("unit", event.currentTarget.value)}
            />
          </Field>
          <datalist id="item-unit-options">
            {UNITS.map((unit) => (
              <option key={unit} value={unit} />
            ))}
          </datalist>
          {!service ? (
            <>
              <Checkbox
                checked={draft.splittable}
                label="Se vende fraccionado"
                onChange={(next) => set("splittable", next)}
              />
              {draft.splittable ? (
                <Field
                  label="Unidades por empaque"
                  htmlFor="item-pack"
                  help="Cuántas unidades base trae el empaque que se compra."
                >
                  <Input
                    id="item-pack"
                    type="number"
                    min={2}
                    value={draft.units_per_pack}
                    onChange={(event) =>
                      set("units_per_pack", event.currentTarget.value)
                    }
                  />
                </Field>
              ) : null}
            </>
          ) : null}
        </Section>

        {!service ? (
          <Section title="Manejo">
            <Checkbox
              checked={draft.requires_prescription}
              label="Requiere fórmula médica"
              onChange={(next) => set("requires_prescription", next)}
            />
            <Checkbox
              checked={draft.controlled}
              label="Medicamento de control especial"
              onChange={(next) => set("controlled", next)}
            />
            <Checkbox
              checked={draft.cold_chain}
              label="Cadena de frío"
              onChange={(next) => set("cold_chain", next)}
            />
            <Checkbox
              checked={draft.tracks_lots}
              label="Maneja lotes"
              onChange={(next) => set("tracks_lots", next)}
            />
            <Checkbox
              checked={draft.tracks_expiry}
              label="Maneja vencimiento"
              onChange={(next) => set("tracks_expiry", next)}
            />
          </Section>
        ) : null}

        <Section title="Impuesto">
          <Field
            label="Clase de IVA"
            htmlFor="item-vat"
            error={vatError}
            required
          >
            <Select
              id="item-vat"
              value={draft.vat_class}
              placeholder="Elija la clase"
              invalid={!!vatError}
              options={enumOptions(VAT_CLASS)}
              onValueChange={(next) => set("vat_class", next as VatClass)}
            />
          </Field>
        </Section>

        {service ? (
          <Section title="Costo del servicio">
            <Field
              label="Costo por unidad"
              htmlFor="item-service-cost"
              optional
              help="Vacío significa sin costo de venta y margen del 100%."
            >
              <Input
                id="item-service-cost"
                inputMode="decimal"
                value={draft.service_cost}
                onChange={(event) =>
                  set("service_cost", event.currentTarget.value)
                }
              />
            </Field>
          </Section>
        ) : null}

        <Section title="Códigos de barras">
          <BarcodeList codes={codes} onChange={setCodes} />
        </Section>

        <Section title="Estado">
          <Checkbox
            checked={draft.active}
            label="Activo en el catálogo"
            onChange={(next) => set("active", next)}
          />
          <Field
            label="Código del sistema anterior"
            htmlFor="item-code"
            optional
          >
            <Input
              id="item-code"
              value={draft.external_code}
              onChange={(event) =>
                set("external_code", event.currentTarget.value)
              }
            />
          </Field>
        </Section>

        {item && elevated ? (
          <>
            <Section title="Precio">
              <PriceSection item={item} locations={locations} />
            </Section>
            {!service ? (
              <Section title="Proveedores">
                <SupplierSection item={item} />
              </Section>
            ) : null}
          </>
        ) : elevated ? (
          <Section title="Precio">
            <Field
              label="Precio de venta"
              htmlFor="item-opening-price"
              optional
              help="Se fija al guardar. Después se cambia aquí mismo, y cada cambio queda con su fecha y con quién lo hizo."
            >
              <Input
                id="item-opening-price"
                inputMode="decimal"
                value={draft.price}
                onChange={(event) => set("price", event.currentTarget.value)}
              />
            </Field>
          </Section>
        ) : item ? (
          <Section title="Precio">
            <span className="text-20 tabular-nums tracking-display text-ink">
              {item.price === null ? "—" : money(Number(item.price))}
            </span>
            <span className="text-11 text-ink-label">
              por {item.unit} · toda la red
            </span>
          </Section>
        ) : null}
      </fieldset>
    </RecordPanel>
  );
}

function Section({
  title,
  first,
  children,
}: {
  title: string;
  first?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        "flex flex-col gap-4",
        first ? "" : "mt-7 border-t border-hairline pt-7",
      )}
    >
      <h3 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
        {title}
      </h3>
      {children}
    </section>
  );
}

/**
 * A list with an add row and one `Principal` radio across the set. A code
 * already held by another item is refused by the server **naming that item**,
 * because an ambiguous scan sells the wrong product at the wrong price.
 */
function BarcodeList({
  codes,
  onChange,
}: {
  codes: { code: string; is_primary: boolean }[];
  onChange: (next: { code: string; is_primary: boolean }[]) => void;
}) {
  const [adding, setAdding] = useState("");
  return (
    <div className="flex flex-col gap-2">
      {codes.length === 0 ? (
        <p className="text-12 text-ink-label">
          Sin códigos. El mostrador lo busca por nombre hasta que la droguería
          imprima uno.
        </p>
      ) : null}
      {codes.map((entry, index) => (
        <div key={entry.code} className="flex items-center gap-2">
          <label className="flex flex-1 items-center gap-2.5">
            <input
              type="radio"
              name="barcode-primary"
              checked={entry.is_primary}
              aria-label={`Código principal ${entry.code}`}
              onChange={() =>
                onChange(
                  codes.map((one, position) => ({
                    ...one,
                    is_primary: position === index,
                  })),
                )
              }
              className="size-[18px] shrink-0 appearance-none rounded-pill border border-edge-strong bg-surface checked:border-ink checked:bg-ink"
            />
            <span className="font-mono text-12 tabular-nums text-ink">
              {entry.code}
            </span>
            {entry.is_primary ? (
              <span className="text-11 text-ink-label">Principal</span>
            ) : null}
          </label>
          <Button
            variant="ghost"
            size="xs"
            iconOnly
            aria-label={`Quitar el código ${entry.code}`}
            onClick={() =>
              onChange(codes.filter((_one, position) => position !== index))
            }
          >
            <Trash2 aria-hidden className="size-3.5" />
          </Button>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <Input
          size="sm"
          value={adding}
          placeholder="Escanee o escriba un código"
          aria-label="Nuevo código de barras"
          onChange={(event) => setAdding(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            addCode();
          }}
        />
        <Button
          variant="secondary"
          size="xs"
          iconOnly
          aria-label="Agregar el código"
          onClick={addCode}
        >
          <Plus aria-hidden className="size-3.5" />
        </Button>
      </div>
    </div>
  );

  function addCode() {
    const code = adding.trim();
    if (!code || codes.some((one) => one.code === code)) return;
    onChange([...codes, { code, is_primary: codes.length === 0 }]);
    setAdding("");
  }
}

/**
 * **The only price field in the product** (A11).
 *
 * It shows the price in force network-wide, any per-sede overrides, and the
 * change history — each history row naming who set it, because
 * `set_by_user_id` exists to answer that question on a screen and not only in
 * a query. Editing creates a new row and closes the old one, and the panel says
 * so rather than pretending the field was overwritten.
 */
function PriceSection({
  item,
  locations,
}: {
  item: ItemDetail;
  locations: Location[];
}) {
  const [amount, setAmount] = useState("");
  const [scope, setScope] = useState("");
  const [error, setError] = useState<string | undefined>();
  const setPrice = useSetPrice();
  const withdraw = useWithdrawPrice();
  const toast = useToast();

  const current = item.prices.filter((row) => row.current);
  const network = current.find((row) => !row.location_id);
  const box =
    item.splittable && network
      ? Number(network.price) * item.units_per_pack
      : undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline gap-3">
        <span className="text-20 tabular-nums tracking-display text-ink">
          {network ? money(Number(network.price)) : "—"}
        </span>
        <span className="text-11 text-ink-label">
          por {item.unit} · toda la red
        </span>
      </div>
      {box !== undefined ? (
        <p className="text-11 text-ink-note">
          El empaque de {item.units_per_pack} {item.unit}s vale {money(box)}. Es
          una cifra derivada: Botica guarda un solo precio, por unidad base.
        </p>
      ) : null}

      <Field
        label="Nuevo precio"
        htmlFor="item-price"
        error={error}
        help={
          error
            ? undefined
            : item.regulated_max_price
              ? `Tope regulado ${money(Number(item.regulated_max_price))}.`
              : // An unknown cap and a cleared one look identical on a screen
                // that says neither (§11.4).
                "Sin tope regulado conocido."
        }
      >
        <div className="flex items-center gap-2">
          <Input
            id="item-price"
            inputMode="decimal"
            value={amount}
            invalid={!!error}
            placeholder={network ? String(network.price) : "0"}
            onChange={(event) => {
              // §B.5.7 · validation fires on blur and on submit, never on
              // keystroke, and a refusal clears the moment the figure changes.
              setError(undefined);
              setAmount(event.currentTarget.value);
            }}
          />
          {locations.length > 0 ? (
            <Select
              aria-label="Alcance del precio"
              value={scope}
              placeholder="Toda la red"
              containerClassName="w-36 shrink-0"
              options={locations.map((sede) => ({
                value: sede.id,
                label: sede.name,
              }))}
              onValueChange={(next) => {
                setError(undefined);
                setScope(next);
              }}
            />
          ) : null}
          <Button
            variant="secondary"
            busy={setPrice.isPending}
            busyLabel="Guardando…"
            onClick={() => {
              if (!amount.trim()) return;
              setPrice.mutate(
                {
                  id: item.id,
                  body: { price: amount.trim(), location_id: scope || null },
                },
                {
                  onSuccess: () => {
                    setAmount("");
                    toast("Se fijó el precio nuevo.");
                  },
                  onError: (failure) =>
                    setError(
                      failure instanceof ApiError
                        ? failure.message
                        : "No pudimos guardar el precio.",
                    ),
                },
              );
            }}
          >
            Fijar
          </Button>
        </div>
      </Field>
      <p className="text-11 text-ink-note">
        Guardar crea una fila nueva y cierra la anterior. Un precio no se
        sobrescribe: es lo que se cobró en su momento.
      </p>

      {current.filter((row) => row.location_id).length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <p className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
            Por sede
          </p>
          {current
            .filter((row) => row.location_id)
            .map((row) => (
              <div key={row.id} className="flex items-center gap-2 text-12">
                <span className="flex-1 truncate text-ink-body">
                  {row.location_name}
                </span>
                <span className="tabular-nums text-ink">
                  {money(Number(row.price))}
                </span>
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={() =>
                    withdraw.mutate(row.id, {
                      onSuccess: (result) => toast(result.detail),
                      onError: (failure) =>
                        toast(
                          failure instanceof ApiError
                            ? failure.message
                            : "No pudimos quitar este precio.",
                        ),
                    })
                  }
                >
                  Quitar
                </Button>
              </div>
            ))}
        </div>
      ) : null}

      <PriceHistory rows={item.prices} />
    </div>
  );
}

function PriceHistory({ rows }: { rows: PriceRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <p className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
        Historial
      </p>
      {rows.slice(0, 8).map((row) => (
        <div key={row.id} className="flex items-baseline gap-2 text-12">
          <span className="tabular-nums text-ink">
            {money(Number(row.price))}
          </span>
          <span className="text-11 text-ink-soft">
            {/* Branched on `source`, not on the presence of a name: a
                hand-typed price whose author was later hard-deleted must never
                read as one the load tool imported. */}
            {row.source === "imported"
              ? `Cargado del sistema anterior · ${dayMonth(row.effective_from)}`
              : row.set_by_name
                ? `Fijado por ${row.set_by_name} · ${dayMonth(row.effective_from)}`
                : `Fijado a mano · ${dayMonth(row.effective_from)}`}
            {row.location_name ? ` · ${row.location_name}` : ""}
            {row.effective_to ? ` · hasta ${dayMonth(row.effective_to)}` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * The item editor is the writer of a `supplier_items` row, so one row has one
 * editor. The settings section lists them read-only and links here.
 */
function SupplierSection({ item }: { item: ItemDetail }) {
  const suppliers = useSuppliers();
  const save = useSaveSupplierItem();
  const remove = useDeleteSupplierItem();
  const toast = useToast();
  const [adding, setAdding] = useState("");

  return (
    <div className="flex flex-col gap-2">
      {item.supplier_items.length === 0 ? (
        <p className="text-12 text-ink-label">
          Sin proveedores. Compras necesita al menos uno para sugerir una orden.
        </p>
      ) : null}
      {item.supplier_items.map((link) => (
        <div key={link.id} className="flex items-center gap-2 text-12">
          <label className="flex flex-1 items-center gap-2.5">
            <input
              type="radio"
              name="preferred-supplier"
              checked={link.is_preferred}
              aria-label={`Proveedor preferido ${link.supplier_name}`}
              onChange={() =>
                save.mutate({ id: link.id, body: { is_preferred: true } })
              }
              className="size-[18px] shrink-0 appearance-none rounded-pill border border-edge-strong bg-surface checked:border-ink checked:bg-ink"
            />
            <span className="min-w-0 flex-1 truncate text-ink">
              {link.supplier_name}
            </span>
          </label>
          <span className="tabular-nums text-ink-body">
            {link.cost === null ? "—" : money(Number(link.cost))}
          </span>
          <Button
            variant="ghost"
            size="xs"
            iconOnly
            aria-label={`Quitar a ${link.supplier_name}`}
            onClick={() =>
              remove.mutate(link.id, {
                onSuccess: () => toast("Se quitó el proveedor."),
              })
            }
          >
            <Trash2 aria-hidden className="size-3.5" />
          </Button>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <Select
          size="sm"
          aria-label="Agregar un proveedor"
          value={adding}
          placeholder="Agregar un proveedor"
          options={(suppliers.data ?? [])
            .filter(
              (one) =>
                !item.supplier_items.some(
                  (link) => link.supplier_id === one.id,
                ),
            )
            .map((one) => ({ value: one.id, label: one.name }))}
          onValueChange={(next) => {
            setAdding("");
            if (!next) return;
            save.mutate(
              {
                body: {
                  supplier_id: next,
                  item_id: item.id,
                  is_preferred: item.supplier_items.length === 0,
                },
              },
              {
                onSuccess: () => toast("Se agregó el proveedor."),
                onError: (failure) =>
                  toast(
                    failure instanceof ApiError
                      ? failure.message
                      : "No pudimos agregar el proveedor.",
                  ),
              },
            );
          }}
        />
      </div>
    </div>
  );
}
