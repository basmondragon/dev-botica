# Botica

The operating platform for Colombian droguerías. `.docs/architecture.md` is the authority; `.docs/stages/` is how it gets built, in order.

**This repository is at S0 — Skeleton.** Four containers against one Postgres, invite-only sign-in, the tenancy and audit substrate every later stage calls rather than rebuilds, and the application shell with one empty route per later-stage surface. It fills none of them.

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

## Where things are

|                         |                                            |
| ----------------------- | ------------------------------------------ |
| `.docs/architecture.md` | the authority — what the system is and why |
| `.docs/stages/`         | how it gets built, in order                |
| `.docs/handoff/`        | the designed screens                       |
| `BLUEPRINT.md`          | what the product is, in one page           |
