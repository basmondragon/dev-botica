import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { buttonClass } from "./button";
import { ROW_HEIGHT } from "./table";
import { Badge, StatusDot, StatusLine } from "./status";
import { EmptyState, RouteError } from "./states";
import { QuantityStepper } from "./stepper";

/**
 * *Verification* 11 · the ported values against Part A's, with a stated expected
 * answer rather than a look. A module that has not been through this list is not
 * ported, it is copied.
 */
describe("the component layer's geometry", () => {
  it("has four button sizes at 30 · 34 · 40 · 52px", () => {
    expect(buttonClass({ size: "xs" })).toContain("h-[30px]");
    expect(buttonClass({ size: "sm" })).toContain("h-[34px]");
    expect(buttonClass({ size: "md" })).toContain("h-10");
    expect(buttonClass({ size: "lg" })).toContain("h-[52px]");
  });

  it("renders every button at --radius-control, which is 9px", () => {
    for (const size of ["xs", "sm", "md", "lg"] as const) {
      expect(buttonClass({ size })).toContain("rounded-control");
    }
  });

  it("renders destructive bordered everywhere except a confirmation's confirm", () => {
    const resting = buttonClass({ variant: "destructive" });
    expect(resting).toContain("border-edge-critical");
    expect(resting).not.toContain("bg-critical ");
    expect(buttonClass({ variant: "destructive", confirming: true })).toContain(
      "bg-critical",
    );
  });

  it("has four table densities at 40 · 44 · 48 · 56px, and no 60", () => {
    expect(Object.keys(ROW_HEIGHT)).toEqual([
      "compact",
      "panel",
      "standard",
      "counter",
    ]);
    expect(ROW_HEIGHT.compact).toBe("h-10");
    expect(ROW_HEIGHT.panel).toBe("h-11");
    expect(ROW_HEIGHT.standard).toBe("h-12");
    expect(ROW_HEIGHT.counter).toBe("h-row-counter");
  });
});

describe("status", () => {
  it("labels a badge in ink on the family's tint, never in the family colour", () => {
    render(<Badge family="warning">Punto de reorden</Badge>);
    const badge = screen.getByText("Punto de reorden");
    expect(badge.className).toContain("text-ink");
    expect(badge.className).toContain("bg-tint-warning");
  });

  it("draws a hollow dot as a ring on a transparent ground", () => {
    const { container } = render(<StatusDot family="warning" dot="hollow" />);
    const dot = container.firstElementChild!;
    expect(dot.className).toContain("border-warning");
    expect(dot.className).toContain("bg-transparent");
  });

  it("shows an incidental status as a dot plus label with no pill", () => {
    const { container } = render(
      <StatusLine family="positive" label="Activo" />,
    );
    expect(container.textContent).toContain("Activo");
    expect(container.innerHTML).not.toContain("bg-tint-positive");
  });
});

describe("empty and error states", () => {
  it("never says `Sin datos`", () => {
    render(
      <EmptyState
        kind="deliberate"
        title="Todavía no hay actividad registrada"
        body="Aquí queda cada cambio."
      />,
    );
    expect(screen.queryByText(/Sin datos/i)).toBeNull();
  });

  it("offers a filtered empty state a secondary action, never a primary", () => {
    render(
      <EmptyState
        kind="filtered"
        title="Ningún producto coincide con estos filtros"
        body="Sede · Chapinero"
        actionLabel="Quitar filtros"
        onAction={() => {}}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Quitar filtros" }).className,
    ).toContain("border-edge-strong");
  });

  it("carries no action on the deliberately-empty kind", () => {
    render(
      <EmptyState
        kind="deliberate"
        title="Resumen de red"
        body="El panel se arma con la venta de las sedes."
        actionLabel="No debería aparecer"
        onAction={() => {}}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("prints a selectable correlation id beside a retry", () => {
    render(
      <RouteError
        title="No pudimos cargar las existencias."
        detail="El servidor no respondió."
        requestId="req_8f2a1c04"
        onRetry={() => {}}
      />,
    );
    const id = screen.getByText("req_8f2a1c04");
    expect(id.className).toContain("select-all");
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeTruthy();
  });
});

describe("the quantity stepper", () => {
  /**
   * §B.5.6 · Botica has two places a number is edited against a proposal -- the
   * `Sugerido` cell in Compras and a ticket line's quantity at the counter --
   * and they are one control. The desktop half is the one S6 uses.
   */
  it("carries its − and + at both densities", () => {
    const { rerender } = render(
      <QuantityStepper value={220} onCommit={() => {}} label="Sugerido" />,
    );
    expect(
      screen.getByRole("button", { name: "Restar una unidad de Sugerido" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Sumar una unidad de Sugerido" }),
    ).toBeTruthy();

    rerender(
      <QuantityStepper
        value={2}
        onCommit={() => {}}
        label="Cantidad"
        density="counter"
      />,
    );
    expect(
      screen.getByRole("button", { name: "Restar una unidad de Cantidad" }),
    ).toBeTruthy();
  });

  it("recedes at zero, as the handoff draws", () => {
    render(<QuantityStepper value={0} onCommit={() => {}} label="Sugerido" />);
    const field = screen.getByLabelText("Sugerido");
    expect(field.className).toContain("bg-canvas");
    expect(field.className).toContain("text-ink-soft");
  });

  it("does not step below its floor", () => {
    render(<QuantityStepper value={0} onCommit={() => {}} label="Sugerido" />);
    expect(
      screen.getByRole("button", { name: "Restar una unidad de Sugerido" }),
    ).toBeDisabled();
  });
});
