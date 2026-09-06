import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { ANDRES, MARCELA, renderWithProviders } from "@/test/harness";
import { ShellSkeleton } from "./shell";
import { SettingsDialog } from "@/settings/settings-dialog";
import { StageRoute } from "./stage-route";
import { NAV } from "./nav";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useLocation: () => ({ pathname: "/dashboard" }),
  useNavigate: () => () => {},
  // As if someone pasted `?settings=people` into the address bar.
  useSearch: () => ({ settings: "people" }),
}));

describe("the shell's first paint", () => {
  /**
   * §B.10.1 · the nav list renders as skeleton items until `/api/me` resolves
   * the role, **never as the seven-item administrator nav that then collapses
   * to two**. A cashier watching five items disappear on every sign-in learns
   * that the application is unsure what they may do, and the flash is also a
   * two-frame advertisement of routes they will be refused.
   */
  it("paints the chrome and never more than two nav placeholders", () => {
    const { container } = renderWithProviders(<ShellSkeleton />);
    expect(screen.getByLabelText("Navegación principal")).toBeTruthy();
    for (const label of NAV.map((item) => item.label)) {
      expect(screen.queryByText(label)).toBeNull();
    }
    // The version stamp is chrome and paints immediately.
    expect(container.textContent).toContain("Botica");
  });
});

describe("the seven empty routes", () => {
  /**
   * §B.10.2 · each renders the **deliberately-empty** kind: a title naming what
   * will live there and a body naming what has to happen first, with no action,
   * because S0 owns none of the actions that fill them.
   */
  it("names what will live there and what has to happen first", () => {
    renderWithProviders(
      <StageRoute
        item={NAV[0]!}
        breadcrumb={["Panel"]}
        title="Resumen de red"
        emptyTitle="Todavía no hay métricas de la red"
        body="El panel se arma con la venta de las sedes. Aparece cuando el mostrador registre las primeras ventas."
      />,
      MARCELA,
    );
    // Exactly one `t-28` title per route, and the empty state names something
    // else -- a second copy of the page title says nothing new.
    expect(screen.getAllByText("Resumen de red")).toHaveLength(1);
    expect(screen.getByText("Todavía no hay métricas de la red")).toBeTruthy();
    expect(screen.getByText(/El panel se arma con la venta/)).toBeTruthy();
    expect(screen.queryByText(/Sin datos/)).toBeNull();
    // No action, because S0 owns none of the actions that fill these routes.
    expect(screen.queryByRole("button")).toBeNull();
  });

  /**
   * §B.8.3 · a route a role cannot reach **refuses inside the content region**,
   * naming the role it needs, and does not redirect silently -- a link that
   * shows nothing is indistinguishable from a broken one.
   */
  it("refuses a cashier inside the content region, naming the role", () => {
    renderWithProviders(
      <StageRoute
        item={NAV.find((item) => item.key === "pricing")!}
        breadcrumb={["Precios"]}
        title="Propuestas"
        emptyTitle="No debería verse."
        body="No debería verse."
      />,
      ANDRES,
    );
    expect(
      screen.getByText(
        /Precios requiere el perfil Propietaria o Administradora/,
      ),
    ).toBeTruthy();
    expect(screen.queryByText("No debería verse.")).toBeNull();
  });
});

describe("the settings dialog", () => {
  /**
   * §B.8.3 · a `cashier` does not reach Ajustes. The gear is absent from their
   * footer and ⌘, opens nothing for them, so the only way here is a pasted
   * `?settings=` param — a search param is a link, and links get shared.
   *
   * **What that param gets is a refusal naming the role, not silence.** S0
   * originally rendered nothing at all; S8's criterion 24 settled it the other
   * way — *"a denial in the content region naming the role required — not a
   * redirect, not a blank pane"* — which is also what `RoleGate` already says
   * about a route: a link that shows nothing is indistinguishable from a broken
   * one. The rail is empty for the role and no section renders behind it.
   */
  it("refuses a cashier by name rather than rendering nothing", () => {
    const { container } = renderWithProviders(
      <SettingsDialog me={ANDRES} />,
      ANDRES,
    );
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    expect(
      screen.getByText(/requiere el perfil Propietaria o Administradora/),
    ).toBeTruthy();
    expect(container.querySelectorAll("nav button")).toHaveLength(0);
  });

  it("opens for an administrator", () => {
    renderWithProviders(<SettingsDialog me={MARCELA} />, MARCELA);
    expect(screen.getByRole("dialog", { name: "Ajustes" })).toBeTruthy();
  });
});
