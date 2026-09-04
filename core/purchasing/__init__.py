"""S6 · purchasing.

The forecast, the suggested order, receiving, supplier returns, the history
loader and the `purchasing` settings group.

**Nothing in this package writes a `stock_moves` row or a `stock_on_hand` row.**
Receiving and supplier returns call `core.inventory.ledger.append`, which is the
one code path in the product that may move a quantity (rule 7, A3) -- and the
database enforces the first half of that, because the runtime role holds no
UPDATE on `stock_moves`.

**Nothing here reaches a device either.** Every surface in this stage is an
office surface, served over the network per view, so S2's sync registry is not
amended (rule 9, §4, A4).
"""
