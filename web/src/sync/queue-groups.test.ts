import { describe, expect, it } from "vitest";
import { QUEUE_LABELS, queueGroups } from "./registry";

/**
 * §B.9.3 · the sync panel's queue line.
 *
 * **A ticket's lines and its payments are counted as the sale they belong to.**
 * A cashier asks how many *sales* are waiting, and eleven rows for one ticket is
 * a number that measures the protocol rather than the queue — which is a
 * question S4 is the first stage to raise, because it is the first whose one
 * document is several outbox rows.
 */
describe("the pending queue, by document", () => {
  it("folds a ticket's parts under the sale", () => {
    expect(
      queueGroups({ sales: 2, sale_lines: 7, payments: 3, customers: 1 }),
    ).toEqual([
      ["customers", 1],
      ["sales", 12],
    ]);
  });

  it("folds a return's lines under the return", () => {
    expect(queueGroups({ sale_returns: 1, sale_return_lines: 2 })).toEqual([
      ["sale_returns", 3],
    ]);
  });

  it("drops what is not pending and labels what is", () => {
    const groups = queueGroups({ sales: 0, shifts: 1 });
    expect(groups).toEqual([["shifts", 1]]);
    expect(QUEUE_LABELS[groups[0]![0]]).toBe("Turnos");
  });
});
