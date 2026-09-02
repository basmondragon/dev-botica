import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DataTable, TableFooter, Tr } from "./table";

interface Row {
  id: string;
  name: string;
}

const ROWS: Row[] = [
  { id: "1", name: "Acetaminofén 500 mg" },
  { id: "2", name: "Ibuprofeno 400 mg" },
];

describe("the table", () => {
  it("draws the current row at --active with the ink inset marker", () => {
    const { container } = render(
      <table>
        <tbody>
          <Tr current>
            <td>fila</td>
          </Tr>
        </tbody>
      </table>,
    );
    const row = container.querySelector("tr")!;
    expect(row.className).toContain("bg-active");
    expect(row.className).toContain("inset_2px_0_0_var(--color-ink)");
  });

  it("draws the keyboard cursor at the hover fill with the grey marker", () => {
    const { container } = render(
      <table>
        <tbody>
          <Tr cursor>
            <td>fila</td>
          </Tr>
        </tbody>
      </table>,
    );
    const row = container.querySelector("tr")!;
    expect(row.className).toContain("bg-hover-row");
    expect(row.className).toContain("inset_2px_0_0_var(--color-ink-soft)");
  });

  it("lets the ink marker win over the grey one", () => {
    const { container } = render(
      <table>
        <tbody>
          <Tr cursor current>
            <td>fila</td>
          </Tr>
        </tbody>
      </table>,
    );
    expect(container.querySelector("tr")!.className).toContain(
      "inset_2px_0_0_var(--color-ink)",
    );
  });

  it("has no zebra striping", () => {
    const { container } = render(
      <DataTable<Row>
        rows={ROWS}
        rowId={(row) => row.id}
        columns={[
          { key: "name", label: "Producto", render: (row) => row.name },
        ]}
      />,
    );
    expect(container.innerHTML).not.toContain("odd:");
    expect(container.innerHTML).not.toContain("even:");
  });

  it("renders the range and the page group only once row_count arrives", () => {
    const { rerender } = render(
      <TableFooter
        page={1}
        pageSize={25}
        rowCount={undefined}
        onPage={() => {}}
        onPageSize={() => {}}
      />,
    );
    // Never `… de muchos`, never a guessed page count.
    expect(screen.queryByText(/de/)).toBeNull();

    rerender(
      <TableFooter
        page={1}
        pageSize={25}
        rowCount={43}
        onPage={() => {}}
        onPageSize={() => {}}
      />,
    );
    expect(screen.getByText("1-25 de 43")).toBeTruthy();
  });

  it("shows a filtered annotation beside the range where a surface has one", () => {
    render(
      <TableFooter
        page={1}
        pageSize={25}
        rowCount={4284}
        annotation="312 requieren acción"
        onPage={() => {}}
        onPageSize={() => {}}
      />,
    );
    expect(screen.getByText("1-25 de 4.284")).toBeTruthy();
    expect(screen.getByText("312 requieren acción")).toBeTruthy();
  });
});

describe("the keyboard layer", () => {
  /**
   * §B.13.2 · `⌘K` is **reserved and bound to nothing** in v1. A list handler
   * that switches on `event.key` alone claims it: pressing ⌘K would move the
   * row cursor, which is exactly the retraining the reservation exists to
   * avoid.
   */
  it("ignores a modified key, so ⌘K does not move the cursor", async () => {
    const { useListKeys } = await import("./use-list-keys");
    const { renderHook, act } = await import("@testing-library/react");

    const { result } = renderHook(() =>
      useListKeys({ rowCount: 5, rowId: (index) => `row-${index}` }),
    );

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "j" }));
    });
    const moved = result.current.cursor;
    expect(moved).toBe(0);

    act(() => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "k", metaKey: true }),
      );
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "j", ctrlKey: true }),
      );
    });
    expect(result.current.cursor).toBe(moved);
  });
});
