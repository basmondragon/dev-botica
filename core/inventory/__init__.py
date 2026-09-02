"""S3 · the stock ledger, the projection, and the four Inventario surfaces.

The package is imported for its side effect as well as its contents: importing
it registers the two push writers this stage owns with S2's push endpoint, so a
receipt line or a counted line arriving from a till goes through the ledger
service rather than through rule 8's generic insert.

**One module writes `stock_on_hand` and it is `ledger`** (ownership.md rule 7).
`api` is the HTTP surface over documents; `jobs` is the digest, the verify and
the rebuild; `states` is the `Estado` derivation; `sync` is the registry
amendment's predicates and the two writers; `demo` is this stage's fixtures.
"""

from core.inventory import sync as _sync

_sync.register()
