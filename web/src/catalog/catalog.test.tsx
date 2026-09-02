import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, MARCELA } from "@/test/harness";
import { INVIMA_STATUS } from "@/ui/status";
import { ItemCombobox } from "./item-combobox";
import { ITEM_TYPE, VAT_CLASS, enumOptions } from "./vocabulary";

/**
 * The query layer is mocked rather than `fetch`: `openapi-fetch` captures
 * `globalThis.fetch` when the client module is imported, so a stub installed
 * afterwards is never called. Mocking the hook asserts the same thing this
 * component is actually responsible for -- **what it asks the server for**.
 */
const asked = vi.fn();

vi.mock("@/api/catalog", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/catalog")>("@/api/catalog");
  return {
    ...actual,
    useItems: (params: unknown) => {
      asked(params);
      return {
        data: { rows: ROWS, row_count: ROWS.length },
        isFetching: false,
      };
    },
  };
});

const ROWS = [
  {
    id: "item-1",
    type: "product" as const,
    name: "Losartán 50 mg × 30",
    presentation: "caja × 30 tabletas",
    manufacturer_id: "lab-1",
    manufacturer_name: "MK",
    category_id: null,
    category_name: null,
    invima_registration: "INVIMA 2019M-0012345",
    invima_status: "valid" as const,
    invima_expires_at: "2027-03-01",
    vat_class: "excluded" as const,
    unit: "caja",
    splittable: false,
    units_per_pack: 1,
    tracks_stock: true,
    active: true,
    price: "18500.00",
  },
  {
    id: "item-2",
    type: "service" as const,
    name: "Toma de presión",
    presentation: "",
    manufacturer_id: null,
    manufacturer_name: null,
    category_id: null,
    category_name: null,
    invima_registration: "",
    invima_status: "not_applicable" as const,
    invima_expires_at: null,
    vat_class: "excluded" as const,
    unit: "servicio",
    splittable: false,
    units_per_pack: 1,
    tracks_stock: false,
    active: true,
    price: "5000.00",
  },
];

beforeEach(() => asked.mockClear());

describe("the catalog's interface vocabulary", () => {
  it("names the four IVA classes, and `excluded` is not a rate", () => {
    // Four values, not three rates plus a spare: `excluded` is not a taxable
    // operation at all and `exempt` is taxable at 0%.
    expect(Object.keys(VAT_CLASS)).toEqual([
      "excluded",
      "exempt",
      "rate_5",
      "rate_19",
    ]);
    expect(VAT_CLASS.excluded).toBe("Excluido de IVA");
    expect(VAT_CLASS.exempt).toBe("Exento de IVA");
  });

  it("names a product and a service in one vocabulary (A7)", () => {
    expect(ITEM_TYPE).toEqual({ product: "Producto", service: "Servicio" });
  });

  it("turns a label map into select options in its declared order", () => {
    expect(enumOptions(ITEM_TYPE)).toEqual([
      { value: "product", label: "Producto" },
      { value: "service", label: "Servicio" },
    ]);
  });
});

describe("the registro INVIMA badge (§B.7.4)", () => {
  it("renders the four states in the families the design system fixes", () => {
    expect(INVIMA_STATUS.valid).toEqual({
      family: "positive",
      dot: "solid",
      label: "Registro vigente",
    });
    // Hollow, because INVIMA has the file: the system is waiting on something
    // outside itself (§B.7.2).
    expect(INVIMA_STATUS.in_process).toEqual({
      family: "warning",
      dot: "hollow",
      label: "En trámite",
    });
    expect(INVIMA_STATUS.expired).toEqual({
      family: "critical",
      dot: "solid",
      label: "Registro vencido",
    });
    expect(INVIMA_STATUS.not_applicable).toEqual({
      family: "neutral",
      dot: "hollow",
      label: "No aplica",
    });
  });

  it("covers every value the API can return, so no row renders a blank cell", () => {
    const fromApi = [
      "valid",
      "in_process",
      "expired",
      "not_applicable",
    ] as const;
    for (const value of fromApi) expect(INVIMA_STATUS[value]).toBeDefined();
  });
});

describe("the catalog combobox (§B.5.4)", () => {
  it("sends the typed term to the server rather than filtering what it holds", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ItemCombobox value="" ariaLabel="Producto" onChange={() => {}} />,
      MARCELA,
    );

    await user.click(screen.getByRole("button", { name: "Producto" }));
    const search = await screen.findByRole("combobox", {
      name: "Nombre, laboratorio, código o registro",
    });
    await user.type(search, "losar");

    expect(asked).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "losar" }),
    );
  });

  it("asks only for active items, so a till never offers a withdrawn one", () => {
    renderWithProviders(
      <ItemCombobox value="" ariaLabel="Producto" onChange={() => {}} />,
      MARCELA,
    );
    expect(asked).toHaveBeenCalledWith(
      expect.objectContaining({ active: "true" }),
    );
  });

  it("names each row by its presentación, so two boxes do not read alike", async () => {
    const user = userEvent.setup();
    const picked = vi.fn();
    renderWithProviders(
      <ItemCombobox value="" ariaLabel="Producto" onChange={picked} />,
      MARCELA,
    );
    await user.click(screen.getByRole("button", { name: "Producto" }));
    const option = await screen.findByRole("option", {
      name: /Losartán 50 mg × 30 · caja × 30 tabletas/,
    });
    await user.click(option);
    expect(picked).toHaveBeenCalledWith(
      "item-1",
      expect.objectContaining({ name: "Losartán 50 mg × 30" }),
    );
  });
});
