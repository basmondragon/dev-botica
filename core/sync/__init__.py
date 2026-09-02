"""The server half of the offline substrate (architecture §5).

**Everything the sync protocol knows lives behind this package.** §5 makes that
a boundary rather than a preference: PowerSync and ElectricSQL were ruled out
with a stated measurement that would change the answer, and the whole point of
declining an engine now is that adopting one later replaces this module and
nothing else. No view outside `core/sync/` issues a cursor query, and no domain
module imports from here except through the two services the ledger names --
the idempotent client-write helper and the registry.

  registry.py   what reaches a device, and under which predicate (rule 9)
  devices.py    the credential, the claim, and the three freshness stamps
  pull.py       the `(updated_at, id)` delta below the safety horizon
  push.py       the idempotent client-write service (rule 8, A5)
  digest.py     the daily divergence check that makes the horizon safe
  conflicts.py  the office's arrival queue
  jobs.py       one job: the daily stale-device check
  api.py        thirteen endpoints, all under `/api/sync` and `/api/devices`
  demo.py       the tills, on every profile
"""
