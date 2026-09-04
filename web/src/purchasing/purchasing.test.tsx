import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PurchaseOrderLineRow } from "@/api/purchasing";
import { QuantityStepper } from "@/ui/stepper";
import {
  BAND_LABEL,
  BASIS_LABEL,
  ORDER_STATUS,
  bandPrefix,
  coverageFamily,
  coverageReading,
  provenanceLine,
  reasonText,
  refreshedAt,
} from "./vocabulary";

/**
 * What this stage owes its screens, checked where a person would look.
 *
 * The rules under test are the ones a reviewer cannot see by reading a
 * component: that the coloured `Cobertura` takes §B.7.4's four bands at the
 * exact edges, that a figure the model cannot compute renders an em dash with a
 * **reason** rather than a zero, that the provenance line is assembled from the
 * window the refresh actually read, and that **no string anywhere hard-codes
 * the handoff's `18 meses`**.
 */

const SRC = join(process.cwd(), "src");

function* walk(directory: string): Generator<string> {
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) yield* walk(full);
    else if (/\.(tsx?|css)$/.test(full)) yield full;
  }
}

function sources() {
  return [...walk(SRC)]
    .filter((file) => !/\.test\.tsx?$/.test(file))
    .filter((file) => !file.includes("schema.gen"))
    .map((file) => ({
      file,
      code: readFileSync(file, "utf8")
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/(^|[^:])\/\/[^\n]*/g, "$1"),
    }));
}

function line(overrides: Partial<PurchaseOrderLineRow> = {}) {
  return {
    id: "line-1",
    item_id: "item-1",
    item_name: "Suero oral 500 ml",
    presentation: "× 6",
    manufacturer_name: "Genfar",
    category_id: null,
    stock: 14,
    weekly_sales: "38.000",
    coverage_days: "3.0",
    suggested_quantity: 220,
    approved_quantity: 220,
    received_quantity: 0,
    unit_cost: "3900.00",
    basis: "learned",
    confidence: "0.820",
    band: "alta",
    reason: "",
    reason_code: "stable_rotation",
    reason_fallback: "Rotación estable, mantiene 45 días",
    stamped_coverage_days: "3.0",
    ...overrides,
  } as PurchaseOrderLineRow;
}

describe("the Cobertura colour", () => {
  /** §B.7.4 · ≤ 4 crítico · 5–20 atención · 21–90 normal · > 90 informativo. */
  it("takes the four bands at the drawn edges", () => {
    expect(coverageFamily(0)).toBe("critical");
    expect(coverageFamily(4)).toBe("critical");
    expect(coverageFamily(5)).toBe("warning");
    expect(coverageFamily(20)).toBe("warning");
    expect(coverageFamily(21)).toBe("neutral");
    expect(coverageFamily(90)).toBe("neutral");
    expect(coverageFamily(91)).toBe("info");
  });

  /**
   * §B.12.3 · **the colour is never the only signal.** The `Por qué` cell
   * always restates the reading in words, so a reader who cannot tell the two
   * warm bands apart still has the sentence.
   */
  it("always has a reason beside it", () => {
    expect(reasonText(line())).toBe("Rotación estable, mantiene 45 días");
    expect(reasonText(line({ reason: "Pico de temporada" }))).toBe(
      "Pico de temporada",
    );
  });

  /**
   * A manual line has no code and never will -- nobody proposed it, so there is
   * nothing for the arithmetic to explain. `Por qué` is the one cell on this
   * screen that must never be blank.
   */
  it("says who wrote a manual line rather than standing empty", () => {
    expect(
      reasonText(
        line({
          basis: null,
          confidence: null,
          band: null,
          reason_code: "",
          reason_fallback: "",
          suggested_quantity: null,
        }),
      ),
    ).toBe("Escrita a mano");
  });
});

describe("a figure the model cannot compute", () => {
  /**
   * §B.9.2 tier 3 · **never a zero.** And the two readings are not the same
   * thing: a null `weekly_sales` is a row with no demand estimate at all, and a
   * zero one is a row the model measured and found nothing in.
   */
  it("says which kind of nothing it is", () => {
    expect(coverageReading(line({ weekly_sales: null }))).toBe("sin histórico");
    expect(coverageReading(line({ weekly_sales: "0.000" }))).toBe(
      "sin ventas en el periodo",
    );
  });
});

describe("the confidence band", () => {
  /**
   * §B.9.2 · **marking the rows that are fine is how a marker stops meaning
   * anything.** The band prefixes the reason only where it is not `Alta`.
   */
  it("is a prefix only where it is not Alta", () => {
    expect(bandPrefix(line({ band: "alta" }))).toBeNull();
    expect(bandPrefix(line({ band: "media" }))).toBe("Media");
    expect(bandPrefix(line({ band: "baja" }))).toBe("Baja");
  });
});

describe("the provenance line", () => {
  /**
   * *UI* · three forms, chosen by the regime the majority of the order's lines
   * were generated in — and the figure is the window the refresh actually read.
   */
  it("names the window it was given", () => {
    expect(provenanceLine("learned", "6 meses", "hoy 06:00", true)).toBe(
      "Modelo entrenado con 6 meses de venta · actualizado hoy 06:00",
    );
    expect(provenanceLine("learning", "3 semanas", "hoy 06:00", true)).toBe(
      "Aprendiendo de 3 semanas de venta propia · actualizado hoy 06:00",
    );
    expect(provenanceLine("parametric", "", "hoy 06:00", true)).toBe(
      "Sin histórico cargado · sugerido por parámetros de la sede · actualizado hoy 06:00",
    );
  });

  /**
   * §10 · **the order is usable and saying so loudly would teach people to
   * distrust a working screen.** The degradation is one clause on the line that
   * was already there, and no banner.
   */
  it("appends the degradation as a clause and nothing more", () => {
    expect(provenanceLine("learned", "6 meses", "hoy 06:00", false)).toBe(
      "Modelo entrenado con 6 meses de venta · actualizado hoy 06:00 · sin redacción del modelo",
    );
  });

  /**
   * The drawn `actualizado hoy 06:00`, and the absolute stamp once it is not
   * today. Never the relative ladder: that marks a figure a till read from its
   * local store, and this is the hour a nightly job ran.
   */
  it("names the hour the refresh ran, not how long ago it was", () => {
    const now = new Date();
    const today = new Date(now);
    today.setHours(6, 0, 0, 0);
    expect(refreshedAt(today.toISOString())).toBe("hoy 06:00");
    const before = new Date(now.getTime() - 8 * 24 * 60 * 60 * 1000);
    expect(refreshedAt(before.toISOString())).toMatch(/^al \d{2}\/\d{2} /);
    expect(refreshedAt(null)).toBe("todavía sin correr");
  });

  /**
   * *UI* · **the drawing yields to the true number.** `18 meses` is what the
   * handoff's own tenant would have produced; a screen carrying it as a literal
   * would tell a prospect the model trained on eighteen months of their sales
   * on a tenant that has none of them.
   */
  it("is never hard-coded anywhere in the product", () => {
    const hits = sources().flatMap(({ file, code }) =>
      /18\s*meses/.test(code) ? [file] : [],
    );
    expect(hits).toEqual([]);
  });
});

describe("the status vocabulary", () => {
  /**
   * §B.7.4 · **`Descartada` is neutral, not critical.** Discarding a suggestion
   * is the product working, and colouring it as a failure would tell an
   * administrator that using their judgement is an error.
   */
  it("colours a discarded order neutrally", () => {
    expect(ORDER_STATUS.discarded.family).toBe("neutral");
    expect(ORDER_STATUS.discarded.label).toBe("Descartada");
  });

  it("declares each of the six labels exactly once", () => {
    const files = sources();
    for (const meaning of Object.values(ORDER_STATUS)) {
      const hits = files.flatMap(({ file, code }) =>
        code.includes(`"${meaning.label}"`) ? [file] : [],
      );
      expect(hits, meaning.label).toHaveLength(1);
      expect(hits[0]).toContain("vocabulary.ts");
    }
  });

  it("names the three regimes and the three bands once each", () => {
    const files = sources();
    for (const label of [
      ...Object.values(BASIS_LABEL),
      ...Object.values(BAND_LABEL),
    ]) {
      const hits = files.flatMap(({ file, code }) =>
        code.includes(`"${label}"`) ? [file] : [],
      );
      expect(hits, label).toHaveLength(1);
    }
  });
});

describe("the Sugerido stepper", () => {
  /**
   * §B.5.6 · commit on `Enter` or blur, revert on `Esc`. This is the control an
   * administrator edits eleven quantities with, and a stepper that lost a value
   * on `Esc` would lose the edit rather than the draft.
   */
  it("commits on Enter and reverts on Escape", async () => {
    const person = userEvent.setup();
    const committed: number[] = [];
    render(
      <QuantityStepper
        value={220}
        label="Cantidad sugerida de Suero oral"
        onCommit={(next) => committed.push(next)}
      />,
    );
    const field = screen.getByLabelText("Cantidad sugerida de Suero oral");
    await person.clear(field);
    await person.type(field, "160{Enter}");
    expect(committed).toEqual([160]);

    await person.clear(field);
    await person.type(field, "9{Escape}");
    expect(committed).toEqual([160]);
    expect(field).toHaveValue("220");
  });
});
