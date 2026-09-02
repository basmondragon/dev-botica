import { render as renderTree, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NON_BREAKING_SPACE } from "@/ui/format";
import { SyncContext, type SyncContextValue } from "./context";
import { render as renderLine } from "./state";
import { SyncPanel } from "./sync-panel";
import { SyncStatus } from "./sync-status";
import type { EngineSnapshot } from "./engine";
import type { DeviceRecord } from "./device";

const DEVICE: DeviceRecord = {
  id: "11111111-1111-1111-1111-111111111111",
  key: "bkd_test",
  label: "Caja 1",
  location_id: "22222222-2222-2222-2222-222222222222",
  location_name: "Chapinero",
  location_code: "CHA",
  persisted: true,
  persistence_dialog_seen: true,
};

function snapshot(patch: Partial<EngineSnapshot> = {}): EngineSnapshot {
  return {
    ready: true,
    progress: {},
    pending: 0,
    queue: {},
    lastPullAt: new Date(Date.now() - 4000).toISOString(),
    lastPushAt: new Date(Date.now() - 9000).toISOString(),
    degraded: null,
    networkFailures: 0,
    online: true,
    clockSkewMs: 120,
    clockSkewWarnSeconds: 90,
    storagePersisted: true,
    device: DEVICE,
    syncing: null,
    lastError: "",
    requestId: "",
    ...patch,
  };
}

function mount(
  ui: React.ReactNode,
  patch: Partial<EngineSnapshot> = {},
  extra: Partial<SyncContextValue> = {},
) {
  const current = snapshot(patch);
  const value: SyncContextValue = {
    database: null,
    device: DEVICE,
    snapshot: current,
    line: renderLine({
      blocking: false,
      degraded: current.degraded,
      online: current.online,
      networkFailures: current.networkFailures,
      pending: current.pending,
      lastPullAt: current.lastPullAt,
    }),
    registry: null,
    needsClaim: false,
    syncNow: vi.fn(),
    retryNow: vi.fn(),
    adopt: vi.fn(),
    panelOpen: false,
    setPanelOpen: vi.fn(),
    ...extra,
  };
  return {
    value,
    ...renderTree(<SyncContext value={value}>{ui}</SyncContext>),
  };
}

describe("§B.9.1 · SyncStatus", () => {
  it("is bare text with no dot at rest, on every placement", () => {
    // Criterion 20 · a green dot on every screen all day is decoration, and a
    // decoration that is always there is one nobody reads when it changes.
    const office = mount(<SyncStatus />);
    // The visible line, read off the DOM rather than through `getByText`, which
    // normalises the non-breaking space away — and the space is the point
    // (§A.11).
    expect(office.container.querySelector("span > span")?.textContent).toBe(
      `Sincronizado hace 4${NON_BREAKING_SPACE}s`,
    );
    expect(
      office.container.querySelector("span[aria-hidden]"),
    ).not.toBeInTheDocument();
    office.unmount();

    const counter = mount(<SyncStatus placement="counter" />);
    expect(
      counter.container.querySelector("span[aria-hidden]"),
    ).not.toBeInTheDocument();
  });

  it("shows the dot only when the state leaves synced", () => {
    const { container } = mount(<SyncStatus />, { pending: 3 });
    // Twice, deliberately: the line a cashier reads and the line a screen
    // reader is told. `pending` is a state change, so both carry it.
    expect(screen.getAllByText("Sincronizando · 3 pendientes")).toHaveLength(2);
    expect(container.querySelector("span[aria-hidden]")).toBeInTheDocument();
  });

  it("announces the state and never the ticking clock", () => {
    // §B.9.1 · a screen reader is told when the till goes offline, and is not
    // told twelve times a minute that it is still synced.
    const resting = mount(<SyncStatus />);
    expect(
      resting.container.querySelector('[role="status"]')?.textContent,
    ).toBe("Sincronizado");
    expect(resting.container.querySelector(".animate-pulse")).toBeNull();
    resting.unmount();

    const { container } = mount(<SyncStatus />, { pending: 1 });
    const region = container.querySelector('[role="status"]');
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region?.textContent).toBe("Sincronizando · 1 pendiente");
  });

  it("opens the panel from a 44px hit target on a counter surface", async () => {
    const setPanelOpen = vi.fn();
    mount(<SyncStatus placement="counter" />, {}, { setPanelOpen });
    await userEvent.click(
      screen.getByRole("button", { name: /Estado de sincronización/ }),
    );
    expect(setPanelOpen).toHaveBeenCalledWith(true);
  });
});

describe("§B.9.3 · the sync panel", () => {
  it("carries both stamps, the queue by kind, the device and its sede", () => {
    mount(
      <SyncPanel />,
      { pending: 1, queue: { customers: 1 } },
      {
        panelOpen: true,
      },
    );
    expect(screen.getByText("Última descarga")).toBeInTheDocument();
    expect(screen.getByText("Último envío")).toBeInTheDocument();
    expect(screen.getByText("Clientes 1")).toBeInTheDocument();
    expect(screen.getByText("Caja 1")).toBeInTheDocument();
    expect(screen.getByText("Chapinero")).toBeInTheDocument();
    expect(screen.getByText("Almacenamiento protegido")).toBeInTheDocument();
  });

  it("carries no numbering read-out, and no zero standing in for one", () => {
    // A6, §8 · nothing allocates a fiscal range, so the slot is **absent, not
    // zeroed** (§B.9.2 tier 3). Nothing at v1 fills it.
    const { container } = mount(<SyncPanel />, {}, { panelOpen: true });
    expect(container.textContent).not.toMatch(/numeraci[oó]n/i);
    expect(container.textContent).not.toMatch(/resoluci[oó]n/i);
  });

  it("states the clock skew only past the threshold, and never corrects it", () => {
    const quiet = mount(
      <SyncPanel />,
      { clockSkewMs: 120 },
      { panelOpen: true },
    );
    expect(quiet.container.textContent).not.toMatch(/reloj/i);
    quiet.unmount();

    mount(<SyncPanel />, { clockSkewMs: 240_000 }, { panelOpen: true });
    expect(
      screen.getByText(/El reloj de este equipo está 4 min adelantado/),
    ).toBeInTheDocument();
  });

  it("names the persistence refusal and keeps the till selling", () => {
    mount(<SyncPanel />, { storagePersisted: false }, { panelOpen: true });
    expect(screen.getByText("Almacenamiento sin proteger")).toBeInTheDocument();
    expect(screen.getByText(/Este equipo sigue vendiendo/)).toBeInTheDocument();
  });
});
