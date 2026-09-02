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
