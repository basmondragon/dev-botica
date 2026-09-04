import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import type { SaleFiscal } from "@/api/fiscal";
import { renderWithProviders } from "@/test/harness";
import { FISCAL_STATUS, WORK_LIST } from "./vocabulary";
import { SaleFiscalReadOut } from "./sale-read-out";
import { Empty } from "./work-list";

/**
 * What this stage owes its screens, checked where a person would look.
 *
 * The rules under test are the ones a reviewer cannot see by reading a
 * component: that the four status labels exist **once each** as literals, that
 * **no string anywhere claims a DIAN outcome**, and that the sale's read-out
 * renders **nothing at all** in every state but one.
 */

// From the working directory rather than from `import.meta.url`: this file
// runs under jsdom, where `import.meta.url` is not a `file:` URL, and vitest
// runs from `web/`.
const SRC = join(process.cwd(), "src");

function* walk(directory: string): Generator<string> {
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) yield* walk(full);
    else if (/\.(tsx?|css)$/.test(full)) yield full;
  }
}

/**
 * The product's own source, with comments blanked: a rule reads code, not the
 * prose that explains it, and this file's own prose quotes every label it
 * forbids.
 *
 * **Test files are excluded, and deliberately.** A check that asserts a string
 * is absent has to name it -- S2's own guard greps the till's bundle for `CUDE`
 * and `CUFE` -- so scanning the checks would make every guard in the tree its
 * own violation.
 */
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

describe("the handoff's canonical copy", () => {
  /**
   * *Verification* · the bundle grep, run over the source rather than the
   * bundle so a failure names the file.
   *
   * **More than once means a second surface renders a status this stage owns**,
   * and the two will diverge; zero means a label was translated or paraphrased
   * at the point of use.
   */
  it("declares each of the four status labels exactly once", () => {
    const files = sources();
    for (const meaning of Object.values(FISCAL_STATUS)) {
      const hits = files.flatMap(({ file, code }) =>
        code.includes(`"${meaning.label}"`) ? [file] : [],
      );
      expect(hits, meaning.label).toHaveLength(1);
      expect(hits[0]).toContain("vocabulary.ts");
    }
  });

  it("declares the route's name once", () => {
    const hits = sources().flatMap(({ file, code }) =>
      code.includes(`"${WORK_LIST}"`) ? [file] : [],
    );
    expect(hits).toHaveLength(1);
  });

  /**
   * **No label may claim a DIAN outcome.** `Aceptado por la DIAN` on a row
   * Botica delivered to an API is a claim about a filing this product did not
   * perform and cannot see (architecture §8, A9) -- and it is the one badge in
   * the system that would be read as legal cover.
   */
  it("claims no DIAN outcome anywhere in the client", () => {
    const forbidden = [
      /Aceptad[oa] por la DIAN/i,
      /Rechazad[oa] por la DIAN/i,
      /\bCUFE\b/,
      /enviad[oa] a la DIAN/i,
      /transmitid[oa] a la DIAN/i,
    ];
    const offenders = sources().flatMap(({ file, code }) =>
      forbidden.some((pattern) => pattern.test(code)) ? [file] : [],
    );
    expect(offenders).toEqual([]);
  });

  /**
   * §B.7.2 · solid means the state is true now; hollow means it is not yet
   * true, or it is waiting on something outside this system. `pending` and
   * `sent` are both waiting on the client's invoicing system.
   */
  it("keeps §B.7.4's families and dots", () => {
    expect(FISCAL_STATUS.pending).toMatchObject({
      family: "neutral",
      dot: "hollow",
    });
    expect(FISCAL_STATUS.sent).toMatchObject({ family: "info", dot: "hollow" });
    expect(FISCAL_STATUS.acknowledged).toMatchObject({
      family: "positive",
      dot: "solid",
    });
    expect(FISCAL_STATUS.failed).toMatchObject({
      family: "critical",
      dot: "solid",
    });
  });
});

/**
 * The read-out is mocked at the hook rather than at `fetch`: `openapi-fetch`
 * captures `globalThis.fetch` when the client module is imported, so a stub
 * installed afterwards is never called.
 */
const answer = vi.hoisted(() => ({ current: null as SaleFiscal | null }));

vi.mock("@/api/fiscal", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/fiscal")>("@/api/fiscal");
  return {
    ...actual,
    useSaleFiscalDocument: () => ({ data: answer.current }),
  };
});

describe("the sale's fiscal read-out", () => {
  /**
   * **Absent, not zero, and not a placeholder** (architecture §8). Four ways to
   * render nothing and one to render a line, which is the right ratio for a
   * region that most instances never fill.
   */
  it("renders nothing while it loads, unconfigured, or with no identifiers", () => {
    for (const state of [
      null,
      { configured: false },
      { configured: true },
      { configured: true, external_number: "" },
    ] as (SaleFiscal | null)[]) {
      answer.current = state;
      const { unmount } = renderWithProviders(
        <SaleFiscalReadOut saleId="a-sale" />,
      );
      // Not `toBeEmptyDOMElement` on the container: the harness mounts the
      // toast root, so what is under test is that **this region** rendered
      // nothing -- no line, no placeholder, no skeleton.
      expect(screen.queryByText(/Factura del sistema/)).toBeNull();
      expect(screen.queryByText(/Ver documento/)).toBeNull();
      unmount();
    }
  });

  it("renders one line naming the client's own invoicing system", () => {
    answer.current = { configured: true, external_number: "FE-4471" };
    renderWithProviders(<SaleFiscalReadOut saleId="a-sale" />);
    const line = screen.getByText(/FE-4471/);
    expect(line.textContent).toContain("Factura del sistema de facturación");
    // No badge, in any state, at any density.
    expect(screen.queryByText("Confirmado")).toBeNull();
  });
});

/**
 * The empty states, which is where §B.10.2's "conflating them is the defect"
 * actually bites: an empty list means one of two opposite things — nothing is
 * connected, or everything has settled — and the copy for one is false in the
 * other.
 */
describe("the work list's empty states", () => {
  const configure = vi.fn();

  it("says nothing until the summary has answered", () => {
    // A kind is a claim. While the summary is in flight — or for ever, if it
    // errored — the only honest empty state is none: the deliberate copy
    // asserts a system was connected, which is false on the instance that
    // ships.
    renderWithProviders(
      <Empty
        segment="pendientes"
        filtered={false}
        summary={undefined}
        onConfigure={configure}
      />,
    );
    // Not `toBeEmptyDOMElement` on the container: the harness mounts the toast
    // root. What is under test is that **this component** claims nothing.
    expect(screen.queryByText(/sistema de facturación/)).toBeNull();
    expect(screen.queryByText(/No hay envíos/)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it.each(["pendientes", "fallidos", "sin-enviar"] as const)(
    "offers Configurar on the %s list when nothing is connected",
    (segment) => {
      renderWithProviders(
        <Empty
          segment={segment}
          filtered={false}
          summary={{ configured: false }}
          onConfigure={configure}
        />,
      );
      expect(
        screen.getByText("No hay ningún sistema de facturación conectado"),
      ).toBeTruthy();
      // The action is the route's only pointer at the one surface permitted to
      // name the handoff being off — and `EmptyState` drops it for the
      // deliberate kind, so the kind and the action have to agree.
      expect(screen.getByRole("button", { name: "Configurar" })).toBeTruthy();
    },
  );

  it.each(["pendientes", "fallidos", "sin-enviar"] as const)(
    "claims a connection on the %s list only once there is one",
    (segment) => {
      renderWithProviders(
        <Empty
          segment={segment}
          filtered={false}
          summary={{ configured: true, unsent: 0, failed: 0 }}
          onConfigure={configure}
        />,
      );
      expect(screen.queryByText(/No hay ningún sistema/)).toBeNull();
      expect(screen.queryByRole("button", { name: "Configurar" })).toBeNull();
    },
  );
});
