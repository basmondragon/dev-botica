import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { MARCELA, ANDRES, renderWithProviders } from "@/test/harness";
import { count } from "@/ui/format";
import { LotTrace } from "./lot-trace";

/**
 * §1 deliverable 6, acceptance 10 · **the recall answer, as a screen.** The two
 * properties a reviewer cannot see by reading the component: that the trace is
 * ordered and carries the device and the user on every line, and that the
 * running balance is shown against the shelf so a disagreement between them is
 * legible rather than hidden.
 */

const LOT = {
  id: "lot-1",
  item_id: "item-1",
  item_name: "Acetaminofén 500 mg × 100",
  lot_code: "A-2291",
  expires_at: "2027-03-31",
  supplier_id: "sup-1",
  supplier_name: "Distribuidora Andina",
  unit_cost: "1200.00",
  invima_registration: "INVIMA 2019M-0012345",
  total: 88,
  by_location: [
    { location_id: "loc-1", location_name: "Chapinero", quantity: 40 },
    { location_id: "loc-2", location_name: "Suba", quantity: 48 },
  ],
};

function trace(balance: number) {
  return {
    lot: LOT,
    moves: [
      {
        id: "move-1",
        location_id: "loc-1",
        location_name: "Chapinero",
        item_id: "item-1",
        item_name: LOT.item_name,
        lot_id: "lot-1",
        lot_code: "A-2291",
        quantity: 100,
        type: "adjustment",
        reason: "opening_stock",
        note: "",
        document_type: "receipts",
        document_id: "doc-1",
        unit_cost: "1200.00",
        occurred_at: "2026-08-01T14:02:00.000000Z",
        recorded_at: "2026-08-01T14:02:03.000000Z",
        device_id: "dev-1",
        device_label: "Caja 1",
        user_id: "user-1",
        user_name: "Marcela Ríos",
        fefo_override: false,
        balance: 100,
      },
      {
        id: "move-2",
        location_id: "loc-1",
        location_name: "Chapinero",
        item_id: "item-1",
        item_name: LOT.item_name,
        lot_id: "lot-1",
        lot_code: "A-2291",
        quantity: -12,
        type: "shrinkage",
        reason: "damage",
        note: "",
        document_type: "",
        document_id: null,
        unit_cost: null,
        occurred_at: "2026-08-20T09:15:00.000000Z",
        recorded_at: "2026-08-20T09:15:01.000000Z",
        device_id: null,
        device_label: null,
        user_id: "user-1",
        user_name: "Marcela Ríos",
        fefo_override: false,
        balance,
      },
    ],
  };
}

const answers = vi.hoisted(() => ({
  trace: null as unknown,
  lot: null as unknown,
}));

vi.mock("@/api/inventory", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/inventory")>("@/api/inventory");
  return {
    ...actual,
    useTrace: (id: string | null) => ({
      data: id ? answers.trace : undefined,
      isPending: false,
      isError: false,
      error: null,
    }),
    useLot: (id: string | null) => ({
      data: id ? answers.lot : undefined,
      isPending: false,
      isError: false,
      error: null,
    }),
  };
});

describe("acceptance 10 · the recall answer", () => {
  it("names the lot, every sede holding it, and every move with its device and user", () => {
    answers.trace = trace(88);
    answers.lot = LOT;
    renderWithProviders(
      <LotTrace lotId="lot-1" me={MARCELA} onClose={() => undefined} />,
    );

    expect(screen.getByText("A-2291")).toBeTruthy();
    expect(screen.getByText("INVIMA 2019M-0012345")).toBeTruthy();
    // The reverse lookup: a code in, every sede holding it out.
    const holders = screen.getByRole("list");
    expect(within(holders).getByText("Chapinero")).toBeTruthy();
    expect(within(holders).getByText("Suba")).toBeTruthy();

    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    // Header plus the two moves, in `recorded_at` order.
    expect(rows).toHaveLength(3);
    expect(within(rows[1]!).getByText(/Marcela Ríos · Caja 1/)).toBeTruthy();
    // A move with no device is an em dash on the device, never a blank cell.
    expect(within(rows[2]!).getByText("Marcela Ríos")).toBeTruthy();
    expect(within(rows[2]!).getByText(count(-12))).toBeTruthy();
  });

  it("says so when the running balance disagrees with the shelf", () => {
    // A final balance that disagrees with the projection is a trace nobody can
    // hand to an inspector, so the two are shown together.
    answers.trace = trace(80);
    answers.lot = LOT;
    renderWithProviders(
      <LotTrace lotId="lot-1" me={MARCELA} onClose={() => undefined} />,
    );
    expect(
      screen.getByText(/el recorrido y la existencia no coinciden/),
    ).toBeTruthy();
  });

  it("gives a cashier the lot and its sedes and not the move-by-move trace", () => {
    // §2 grants a cashier a network-wide stock lookup; the trace itself is
    // `owner`/`admin` on the server, and asking for it anyway would render a
    // 403 as a broken screen.
    answers.trace = trace(88);
    answers.lot = LOT;
    renderWithProviders(
      <LotTrace lotId="lot-1" me={ANDRES} onClose={() => undefined} />,
    );
    expect(within(screen.getByRole("list")).getByText("Suba")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("Descargar CSV")).toBeNull();
  });
});
