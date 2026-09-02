# Botica

The operating platform for Colombian droguerías. `.docs/architecture.md` is the authority; `.docs/stages/` is how it gets built, in order.

**This repository is at S2 — Sync.** S0 built four containers against one Postgres, invite-only sign-in, the tenancy and audit substrate every later stage calls rather than rebuilds, and the application shell with one empty route per later-stage surface. S1 filled the first of them: one table for products and services, the registro INVIMA, IVA per line, the price editor that is the only thing in Botica that changes a price, an internal load tool, and a demo seed that builds Droguerías La 45 with 4.284 references. S2 turns a browser at a counter into a **device**: it downloads that sede's operating set once, reads it at zero latency with the network gone, queues what it writes in a local outbox, and drains that outbox exactly once when the link returns — including when the push times out after the server already committed.

## Bring it up

```
make setup          # the env file from its example, a 3.13 venv, node modules
make up             # web · worker · postgres · caddy
```

For day-to-day work the database runs in Docker and everything else runs locally:

```
make db             # just Postgres, on 127.0.0.1:5434
make migrate        # as the migration role, which owns every table
make platform-admin EMAIL=you@example.com NAME="Your name"
make seed PROFILE=default
make seed PROFILE=minimal
make web            # Vite on :5173, proxying /api to Django
```

`make migrate TARGET="core 0004"` runs the graph backwards, which is how a stage
checks that its own migrations reverse cleanly to the one before it.

Loading a real network's master data is a management command and not a screen —
there is no tenant-facing import wizard in v1:

```
.venv/bin/python manage.py load_catalog --tenant demo-la-45 --dir ./export
.venv/bin/python manage.py load_catalog --tenant demo-la-45 --dir ./export --apply
.venv/bin/python manage.py load_catalog --tenant demo-la-45 --dir . --columns
```

**A run is a dry run unless `--apply` is passed**, it writes an `imports` row
either way, and it exits non-zero if any row failed.

`make seed` prints what it wrote, the tenant id, and the accept link of every outstanding invitation — the only place those tokens exist outside the email, and admissible only because the command refuses any tenant whose slug does not begin `demo-`.

## The gates

```
make check          # check_rls · ruff · mypy · pytest · typecheck · lint · format · conformance · vitest · schema
```

Two of them are the ones that matter and are invisible in every other check:

- **`make rls`** — one query reporting `relrowsecurity` and `relforcerowsecurity` per table, one reporting each table's owner, and one reporting the runtime role's grants on `audit_log`. Row security off, or on but not forced, is a table the runtime role reads across tenants. A table owned by the runtime role bypasses its own policy whatever the policy says.
- **`npm --prefix web run conformance`** — the §B.16 greps: the eight type steps, the seven radii under Botica's own names, one focus ring, no hex literal in a component, nothing that translates, and no `dark:` variant anywhere.

## What S0 hands the chain

| | |
| --- | --- |
| `core/tenancy.py` | tenant pinning for all five contexts of ledger rule 6, plus the one registry-shaped unpinned resolution. **No later stage issues its own `SET LOCAL`.** |
| `core/scoping.py` | the location predicate in its two modes, raising rather than defaulting on a cashier with no home sede (A2) |
| `core/audit.py` | the audit write path. `audit_log` is append-only by grant, not by convention |
| `core/tenant_settings.py` | one `jsonb_set` per key group, raising on a zero-row update (ledger rule 5) |
| `core/grid.py` | the server half of the grid contract: `{ rows, row_count, page, page_size }`, `row_count` after filters and before pagination |
| `core/demo/` | `seed_demo_tenant`, its five profiles, the two-part guard and the fixture registry every later stage hooks into |
| `web/src/ui/` | the design-system Part B component layer, including the counter-density tokens S4 selects and the four chart forms S9 uses |
| `web/src/ui/format.ts` | the single Colombian formatter. The only place `Intl` is touched |
| `web/src/shell/` | the shell, its seven role-gated routes and the settings dialog |

## What S1 hands the chain

| | |
| --- | --- |
| `items` and its three switches | `tracks_stock`, `tracks_lots`, `tracks_expiry` — what S3 reads to decide what a movement means. One table for products and services (A7) |
| the base-unit rule | every quantity in the rest of the product is in `items.unit`, and `units_per_pack` is the only conversion |
| `item_prices` | the resolution rule, and **one interactive write path**: `POST /api/items/{id}/prices`. S7 extends it and ships no second one (A11) |
| `item_barcodes` | a code unique per tenant, so a scan resolves to exactly one item — what S2 syncs and S4 depends on at 50ms |
| `customers` | for S4 to create at the counter and S5 to read as the acquirer. The Ley 1581 deletion rule is settled here |
| `imports` | for S6's sales-history loader to record its runs in without a migration |
| `core/catalog/demo.py` | the `catalog` fixture, the root every later stage's fixture declares a dependency on behind S0's identity fixture |
| `web/src/catalog/item-combobox.tsx` | the catalog combobox S3's receiving, S4's product lookup, S6's order lines and S7's price grid all read |

## What S2 hands the chain

| | |
| --- | --- |
| `core/sync/registry.py` | **the declared set of collections that reach a device**, with a predicate and a per-location estimate each. A table absent from it does not reach a till, and adding one is an edit to this file and to S2's stage document (ledger rule 9, A4) |
| `core/sync/push.py` | the idempotent client-write service. One batch, one pinned transaction, per-row outcomes, dedupe on `(tenant_id, client_uuid)` or a declared natural key. **S3, S4, S5 and S8 call it and none dedupes by hand** (rule 8, A5) |
| `core/sync/pull.py` | the `(updated_at, id)` tuple cursor below a safety horizon. A stage adding a collection adds a predicate and an index, not an endpoint |
| the delta cursor indexes | `(tenant_id, location_id, updated_at, id)` for a location-scoped collection, `(tenant_id, updated_at, id)` for a tenant-wide one (rule 4) |
| `devices` | the unit of sync and of blame: its sede, its label, its hashed key, both freshness stamps, its clock skew, its storage state — and `code`, which S4 composes `sales.number` from |
| `sync_conflicts` and its three enums | the office's arrival queue. **S3 writes the negative-stock rows and S4 writes `stale_price` and `catalog_divergence`**; the enum carries all nine values from creation, so neither ships an `ALTER TYPE` |
| `web/src/sync/` | the whole client half behind one module — the local store, the outbox, the poll schedule and its backoff, `SyncStatus` and its five states, the sync panel. If §4's measurements ever justify a sync engine it replaces this module and nothing else (§5) |
| the outbox | one local-only queue every later stage writes into. S3 and S4 add a kind to it and a line to the panel; neither builds a second |

## Where things are

|                         |                                            |
| ----------------------- | ------------------------------------------ |
| `.docs/architecture.md` | the authority — what the system is and why |
| `.docs/stages/`         | how it gets built, in order                |
| `.docs/handoff/`        | the designed screens                       |
| `BLUEPRINT.md`          | what the product is, in one page           |
