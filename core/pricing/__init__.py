"""S7 · pricing. The analysis behind a price, and the regulated cap that bounds it.

**Nothing in this package writes a price** (A11). There is no `INSERT` and no
`UPDATE` on `item_prices` anywhere below this line, there is no `model` value in
`price_source` to record one under, and there is no scheduled repricing and no
bulk apply. What this stage produces is a suggestion with its arithmetic in the
open; what turns one into a price is a person, in S1's own editor, one reference
at a time, with their name on the row.

Two engines, because the screen has to be useful on the first morning (§1):

  * `estimator` -- a constant-elasticity log-log fit on weekly aggregates over
    26 weeks, which **withholds rather than guessing** and hands the reference
    to the second engine when it does.
  * `engine` -- the margin rule, which needs no history at all, plus the
    precedence between the two, the bounded step, the rounding and the gates.

`caps` holds the guardrail, `jobs` the weekly run and the daily cap check, `api`
the routes, and `demo` the fixture -- which runs these engines rather than
inserting suggestion rows, because a fixture that bypasses the engine keeps
rendering a convincing screen after the engine has broken.
"""
