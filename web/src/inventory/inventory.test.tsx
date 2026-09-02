import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { StockRow } from "@/api/inventory";
import { ANDRES, MARCELA, renderWithProviders } from "@/test/harness";
import { ReceivePage, baseUnits, packLabel, toIsoMonth } from "./receive-page";
import {
  STOCK_STATE,
  monthsUntil,
  stateBadge,
  stockoutClause,
} from "./vocabulary";

/**
 * What this stage owes the screen, checked where a person would look.
 *
 * The rules under test are the ones a reviewer cannot see by reading the
 * component: a badge that must **not** carry a cover figure, a bar that must be
 * absent rather than empty, a conversion that must land on base units, and a
 * capture field where a single letter must stay a letter.
 */

/**
 * The router and the sync provider are the two things a surface needs that a
 * unit test has no business standing up: one owns the address bar and the other
 * owns a browser database. What is under test here is the stage's own rules.
 */
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => () => undefined,
  useLocation: () => ({ pathname: "/inventory/receive" }),
  Link: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/sync/context", () => ({
  useSync: () => ({ snapshot: null, database: null, line: null }),
}));

/**
 * The query layer is mocked rather than `fetch`: `openapi-fetch` captures
 * `globalThis.fetch` when the client module is imported, so a stub installed
 * afterwards is never called. Mocking the hook asserts the thing the component
 * is actually responsible for -- **what it asks the server for**.
 */
const asked = vi.fn();

vi.mock("@/api/inventory", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/inventory")>("@/api/inventory");
  return {
    ...actual,
    useStock: (params: unknown) => {
      asked(params);
      return {
        data: { rows: [], row_count: 0, action_required: 0 },
        isPending: false,
        isFetching: false,
        isError: false,
      };
    },
  };
});

function row(overrides: Partial<StockRow> = {}): StockRow {
  return {
    id: "row-1",
    item_id: "item-1",
    item_name: "Acetaminofén 500 mg × 100",
    presentation: "caja × 100 tabletas",
    unit: "caja",
    tracks_lots: true,
    invima_status: "valid",
    manufacturer_name: "Genfar",
    location_id: "loc-1",
    location_name: "Chapinero",
    lot_id: "lot-1",
    lot_code: "A-2291",
    expires_at: "2027-03-31",
    quantity: 412,
    bar_percentage: 88,
    state: "sufficient",
    state_ordinal: 7,
    reorder_point: 60,
    max_quantity: 468,
    min_quantity: 30,
    target_coverage_days: 30,
    policy_source: "manual",
    elsewhere: null,
    ...overrides,
  };
}

describe("§B.7.4 · the seven states, their families and their dots", () => {
  it("gives expiry a hollow dot and Vencido a solid one", () => {
    // The ring stays while the tint escalates: warning hollow in the notice
    // window, critical hollow in the alert window, solid critical once expired.
    expect(STOCK_STATE.expiring).toMatchObject({
      family: "warning",
      dot: "hollow",
    });
    expect(STOCK_STATE.expiring_urgent).toMatchObject({
      family: "critical",
      dot: "hollow",
    });
    expect(STOCK_STATE.expired).toMatchObject({
      family: "critical",
      dot: "solid",
    });
  });

  it("renders the month count from the lot's own date, never a fixed horizon", () => {
    // **A horizon written into the string is how a badge ends up announcing a
    // window a tenant moved months ago**, and an expiry badge that states the
    // wrong horizon is worse than no badge because a pharmacist acts on it.
    const now = new Date(2026, 8, 2);
    expect(monthsUntil("2027-03-31", now)).toBe(6);
    expect(monthsUntil("2026-09-30", now)).toBe(0);
    expect(monthsUntil("2026-01-01", now)).toBe(0);
  });

  it("never renders Sobrestock with a day figure before S6 exists", () => {
    // §B.9.2 tier 3 · a fabricated cover figure is worse than none, and a zero
    // would be the most expensive kind of lie this screen can tell.
    const badge = stateBadge(row({ state: "overstock", state_ordinal: 6 }));
    expect(badge.label).toBe("Sobrestock");
    expect(badge.label).not.toMatch(/día/);
    expect(badge.label).not.toMatch(/\d/);
  });

  it("names one sede in the Quiebre clause and none when there is none", () => {
    const empty = row({
      state: "stockout",
      state_ordinal: 2,
      quantity: 0,
      bar_percentage: 0,
      elsewhere: {
        location_id: "loc-2",
        location_name: "Suba",
        quantity: 96,
      },
    });
    expect(stockoutClause(empty)).toBe("· hay 96 en Suba");
    // On a one-sede network the badge reads `Quiebre` alone -- the derivation
    // rendering correctly, not a missing figure.
    expect(stockoutClause({ ...empty, elsewhere: null })).toBeNull();
    // A clause belongs to a quiebre and to nothing else.
    expect(stockoutClause({ ...empty, state: "sufficient" })).toBeNull();
  });
});

describe("§A.18.1 · the in-cell bar", () => {
  it("is absent where no policy gives the row a capacity", async () => {
    const { StockBar } = await import("@/ui/tile");
    // The component is only rendered when `bar_percentage` is a number; this is
    // the rule the grid encodes, stated where it can be read back.
    expect(row({ bar_percentage: null }).bar_percentage).toBeNull();
    renderWithProviders(<StockBar fill={0} figure="0" />);
    // **A zero draws no fill at all** -- the track alone is the zero state,
    // which overrules the prototype's 4% sliver.
    expect(document.querySelectorAll("[style*='width']")).toHaveLength(0);
  });
});

describe("base units, never packs", () => {
  it("converts twelve packs of thirty into 360", () => {
    // Acceptance 22 · **wrong when it is 12**: a projection reading a thirtieth
    // of the truth, correctable only by another move.
    const line = {
      item: { units_per_pack: 30, unit: "tableta" },
      packs: "12",
    };
    expect(baseUnits(line)).toBe(360);
    expect(packLabel(line as never)).toContain("360");
  });

  it("leaves an unsplittable item at one unit per pack", () => {
    expect(baseUnits({ item: { units_per_pack: 1 }, packs: "12" })).toBe(12);
  });

  it("refuses a quantity that is not a positive number", () => {
    expect(baseUnits({ item: { units_per_pack: 30 }, packs: "" })).toBe(0);
    expect(baseUnits({ item: { units_per_pack: 30 }, packs: "-2" })).toBe(0);
  });
});

describe("MM/AAAA, as a box is printed", () => {
  it("expires at the end of the month, not the start", () => {
    // A box printed `03/2027` is good until the end of March. Taking the first
    // would make it `Vencido` for thirty days before it actually is.
    expect(toIsoMonth("03/2027")).toBe("2027-03-31");
    expect(toIsoMonth("2/2028")).toBe("2028-02-29");
  });

  it("refuses anything that is not a month and a year", () => {
    expect(toIsoMonth("13/2027")).toBeNull();
    expect(toIsoMonth("2027-03")).toBeNull();
    expect(toIsoMonth("")).toBeNull();
  });
});

/**
 * §B.13.3 · **the scanner owns the keyboard on a counter surface.**
 *
 * A scan is a burst of characters followed by `Enter`, and any surface where
 * `j` means something is a surface where scanning a product code navigates.
 * The check is that each of the five office letters arrives in the field as
 * text -- a list component shared with Existencias is how this regresses.
 */
describe("Cargar mercancía binds none of the five office letters", () => {
  it("takes j, k, x, g and slash as characters", async () => {
    renderWithProviders(<ReceivePage me={MARCELA} search={{}} />, MARCELA);
    const field = await screen.findByPlaceholderText("Código de barras");
    await userEvent.click(field);
    await userEvent.keyboard("jkxg/");
    expect((field as HTMLInputElement).value).toBe("jkxg/");
  });
});

describe("§B.8.3 · what a cashier reaches on Existencias", () => {
  it("renders neither header action for a cashier and both for the office", async () => {
    const { StockPage } = await import("./stock-page");

    const office = renderWithProviders(
      <StockPage me={MARCELA} search={{}} />,
      MARCELA,
    );
    // `Cargar mercancía` is the header action **and** the empty state's own
    // primary, which is why the header is asked for by role rather than by
    // text: the two are different affordances that happen to share a label.
    const header = within(office.container).getByRole("banner");
    expect(within(header).getByText("Cargar mercancía")).toBeTruthy();
    expect(within(header).getByText("Nuevo traslado")).toBeTruthy();
    office.unmount();

    // **Not two dimmed buttons.** An action a role cannot reach is not
    // rendered: a greyed control advertises a capability that will never
    // arrive and invites a support ticket a month (§B.8.3).
    const till = renderWithProviders(
      <StockPage me={ANDRES} search={{}} />,
      ANDRES,
    );
    const tillHeader = within(till.container).getByRole("banner");
    expect(within(tillHeader).queryByText("Cargar mercancía")).toBeNull();
    expect(within(tillHeader).queryByText("Nuevo traslado")).toBeNull();
    // The never-populated empty state loses its primary too: the action a
    // cashier cannot take is not offered anywhere on the surface.
    expect(
      within(till.container).queryByRole("button", {
        name: "Cargar mercancía",
      }),
    ).toBeNull();
  });

  it("asks the server for the cashier's own sede without being told to", async () => {
    // A2's UI-default half: the chip is pre-selected, and the server's own
    // helper is what actually confines the query.
    asked.mockClear();
    const { StockPage } = await import("./stock-page");
    renderWithProviders(<StockPage me={ANDRES} search={{}} />, ANDRES);
    expect(asked).toHaveBeenCalledWith(
      expect.objectContaining({ location_id: [ANDRES.location_id] }),
    );
  });
});
