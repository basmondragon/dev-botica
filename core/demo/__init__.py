"""The demo seed: one command, five profiles, one fixture registry.

S0 owns the command, the profile argument, the guard, the registry and the
identity fixture. S1 registers the catalog and every stage after it registers
its own -- and a stage is not finished until its screens render convincingly
from the seed, which is a sharper completion test than a green suite because it
catches the empty state nobody designed and the tile whose denominator is zero.
"""

# Importing the package registers S0's own fixture, which is the root every
# later stage's fixture declares a dependency on.
from core.demo.registry import (
    PROFILES,
    Fixture,
    SeedContext,
    SeedRefused,
    register,
    run_profile,
)

__all__ = [
    "PROFILES",
    "Fixture",
    "SeedContext",
    "SeedRefused",
    "register",
    "run_profile",
]

from core.demo import identity  # noqa: E402,F401  -- registers "identity"

# S1's fixture, and the pattern every later stage follows: one import per stage,
# registering one fixture that declares what it writes and what it needs first.
from core.catalog import demo as catalog  # noqa: E402,F401  -- registers "catalog"

# S2's fixture: the tills. It requires "identity" for `locations` and is what
# S4's fixture will hang its sales on.
from core.sync import demo as sync  # noqa: E402,F401  -- registers "devices"

# S3's fixture: the lots, the moves and the thresholds behind Existencias, plus
# the transfers and counts that keep the module's other three routes off their
# empty states. **It contributes no `stock_on_hand` rows** -- it moves stock
# through the ledger service and lets the projection follow.
from core.inventory import demo as inventory  # noqa: E402,F401  -- "stock"

# S4's fixture: the turnos, the tickets, the payments and the returns -- the
# sales history four later stages learn from, and the only history the product
# has where a client's export does not exist (§1, *Cold start*). It consumes
# stock through S3's ledger service exactly as a real sale does, which is why it
# requires "stock" rather than merely running after it.
from core.counter import demo as counter  # noqa: E402,F401  -- registers "counter"
