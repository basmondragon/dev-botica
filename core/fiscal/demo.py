"""The `fiscal` fixture, registered with **S0's** `seed_demo_tenant`.

**It writes no row, under every one of the five profiles** -- and the
registration is not a formality (§1, ledger cross-stage services). Two things
come out of it.

It puts `fiscal_documents` under the seed's guard, so a foreign row in that table
refuses the whole run: the guard counts every table any fixture declares, and a
table nobody declared is a table the seed would happily leave a stranger's rows
in.

And it states the empty case explicitly rather than omitting it. **The
unconfigured tenant is what the seed ships**, on `default` as on the other four,
because that is what the product ships and the silence of §8 is the behaviour
this stage most needs demonstrable from a bare seed. A seed that arrived
configured would leave the default state rendered by nobody -- and would make
the first verification check, which walks that silence, unrunnable.

**The configured state is reached through the product's own settings screen**,
in a named step, and never by a second command or a sixth profile: a stage that
shipped its own flip to a configured state would be the second seeding path the
ledger forbids, and it would be the one path no reviewer reads because it is not
the one an administrator walks.
"""

from core.demo.registry import register


def build(context):
    """Nothing, deliberately, and the note says so on the command's own report.

    A fixture that wrote a `pending` row would be a fixture that had configured
    a target, and every check in this stage's first walk asserts the opposite.
    """
    context.wrote("fiscal_documents", 0)
    context.note("  facturación     sin conectar (Ajustes · Facturación electrónica)")


def owned_ids(context):
    """No id, because no row. The guard reads this as *the seed owns nothing in
    that table*, so a single row in it refuses the run -- which is exactly the
    guarantee the empty declaration buys."""
    del context
    return {"fiscal_documents": set()}


register(
    "fiscal",
    tables=("fiscal_documents",),
    requires=("counter",),
    build=build,
    owned_ids=owned_ids,
)
