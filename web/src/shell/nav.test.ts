import { describe, expect, it } from "vitest";
import {
  NAV,
  canReach,
  gotoTargets,
  landingFor,
  reachable,
  roleLabel,
} from "./nav";

/**
 * §B.8.3 · the prototype's Mostrador screen draws a `cashier` the full
 * seven-item administrator nav. That is a prototype artefact and this is the
 * test that keeps it out.
 */
describe("role gating", () => {
  it("draws seven items in the drawn order", () => {
    expect(NAV.map((item) => item.label)).toEqual([
      "Panel",
      "Inventario",
      "Compras",
      "Precios",
      "Mostrador",
      "Sedes",
      "Reportes",
    ]);
  });

  it("gives an owner and an admin all seven", () => {
    expect(reachable("owner")).toHaveLength(7);
    expect(reachable("admin")).toHaveLength(7);
  });

  it("gives a cashier Mostrador and a read-only Inventario, and nothing else", () => {
    expect(reachable("cashier").map((item) => item.label)).toEqual([
      "Inventario",
      "Mostrador",
    ]);
    expect(canReach(NAV[0]!, "cashier")).toBe(false);
  });

  it("lands a cashier on Mostrador and everyone else on Panel", () => {
    expect(landingFor("cashier")).toBe("/counter");
    expect(landingFor("owner")).toBe("/dashboard");
    expect(landingFor("admin")).toBe("/dashboard");
  });

  it("reaches only what the role can have from a `g` sequence", () => {
    expect([...gotoTargets("cashier").keys()].sort()).toEqual(["i", "m"]);
    expect(gotoTargets("owner").size).toBe(7);
  });

  it("uses the four Spanish role labels the handoff draws", () => {
    expect(roleLabel("owner")).toBe("Propietaria");
    expect(roleLabel("admin")).toBe("Administradora");
    expect(roleLabel("cashier")).toBe("Mostrador");
    expect(roleLabel("platform_admin")).toBe("Plataforma");
  });
});
