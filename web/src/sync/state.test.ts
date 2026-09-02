import { describe, expect, it } from "vitest";
import { NON_BREAKING_SPACE } from "@/ui/format";
import { DWELL_MS, dwell, render, stateOf, type SyncFacts } from "./state";

const REST: SyncFacts = {
  blocking: false,
  degraded: null,
  online: true,
  networkFailures: 0,
  pending: 0,
  lastPullAt: null,
};

describe("§B.9.1 · the five states, in precedence order", () => {
  it("puts blocked above everything, including offline", () => {
    expect(
      stateOf({ ...REST, blocking: true, online: false, pending: 9 }),
    ).toBe("blocked");
  });

  it("puts degraded above offline and above pending", () => {
    expect(
      stateOf({ ...REST, degraded: "session_expired", online: false }),
    ).toBe("degraded");
  });

  it("reads a call that got no answer as offline, not degraded", () => {
    // The difference is whether we heard back. Telling a cashier the system is
    // failing when the shop's link is simply down teaches them to distrust the
    // line that matters.
    expect(stateOf({ ...REST, networkFailures: 2, pending: 3 })).toBe(
      "offline",
    );
    expect(stateOf({ ...REST, networkFailures: 1, pending: 3 })).toBe(
      "pending",
    );
  });

  it("rests on synced with an empty outbox", () => {
    expect(stateOf(REST)).toBe("synced");
  });
});

describe("§B.9.1 · the line", () => {
  it("is bare text with no dot at rest", () => {
    const now = new Date("2026-09-02T12:00:04Z");
    const line = render({ ...REST, lastPullAt: "2026-09-02T12:00:00Z" }, now);
    // §A.11 · the space between the figure and its unit is U+00A0, so
    // `hace 4 s` never wraps between the number and what it counts.
    expect(line.label).toBe(`Sincronizado hace 4${NON_BREAKING_SPACE}s`);
    expect(line.family).toBeNull();
  });

  it("counts operations and never a percentage", () => {
    expect(render({ ...REST, pending: 3 }).label).toBe(
      "Sincronizando · 3 pendientes",
    );
    expect(render({ ...REST, online: false, pending: 12 }).label).toBe(
      "Sin conexión · 12 por enviar",
    );
  });

  it("names every degraded reason and never says «Error de sincronización»", () => {
    for (const reason of [
      "session_expired",
      "rejected",
      "storage_full",
      "outdated",
      "revoked",
      "evictable",
    ] as const) {
      const line = render({ ...REST, degraded: reason });
      expect(line.label.startsWith("Sincronización con problemas · ")).toBe(
        true,
      );
      expect(line.label).not.toMatch(/Error de sincronización/);
      expect(line.label.split("· ")[1]).toBeTruthy();
    }
  });

  it("never suppresses the pending count behind a degraded reason", () => {
    // The nine unsent rows still exist and the sync panel still shows them;
    // what the line carries is the reason, which is the actionable half.
    const line = render({ ...REST, degraded: "session_expired", pending: 9 });
    expect(line.label).toBe("Sincronización con problemas · sesión vencida");
    expect(stateOf({ ...REST, degraded: "session_expired", pending: 9 })).toBe(
      "degraded",
    );
  });

  it("leaves the blocked string unwritten, because nothing raises it at v1", () => {
    // A6, §8 · the exhausted numbering lease was its only producer and leases
    // are not built. Its geometry is defined; its words belong to the first
    // stage that ever raises it.
    const line = render({ ...REST, blocking: true });
    expect(line.state).toBe("blocked");
    expect(line.label).toBe("");
    expect(line.family).toBe("critical");
  });
});

describe("§B.9.1 · a state dwells two seconds before it is replaced", () => {
  it("holds a non-resting state for its two seconds too", () => {
    // The dwell that matters most is the one leaving `offline`: a link that
    // returns 250 ms after it dropped must not put `Sincronizado` on screen and
    // then take it away again. A hold that only ever applied to `synced` would
    // be a hold that never fired on the transition a cashier actually watches.
    const held = { shown: "offline" as const, shownSince: 0, queued: null };
    const early = dwell({ ...held, shownSince: 1000 }, "synced", 1250);
    expect(early.shown).toBe("offline");
    expect(early.queued).toBe("synced");

    const late = dwell(
      { ...held, shownSince: 1000 },
      "synced",
      1000 + DWELL_MS,
    );
    expect(late.shown).toBe("synced");
    expect(late.queued).toBeNull();
  });

  it("does not let a flapping link flicker the line", () => {
    let held = { shown: "synced" as const, shownSince: 0, queued: null };
    let state = dwell(held, "offline", 100);
    expect(state.shown).toBe("synced");
    state = dwell(state, "synced", 300);
    expect(state.shown).toBe("synced");
    state = dwell(state, "offline", 500);
    expect(state.shown).toBe("synced");

    // Past the dwell, the change lands.
    held = { shown: "synced", shownSince: 0, queued: null };
    expect(dwell(held, "offline", DWELL_MS).shown).toBe("offline");
  });
});
