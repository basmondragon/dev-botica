---
type: index
doc: botica-v1-stages
company: "[[particula-tech]]"
product: "Botica"
captured: "2026-09-01"
status: draft
source: "[[botica-v1-architecture]]"
---

# Botica — Build Stages

Eleven stage documents split out of [`../architecture.md`](../architecture.md). Each one is scoped so a build agent can work from that document, plus the architecture and the ledger, without reading the other ten.

`architecture.md` is the authority. These documents decompose it; they do not extend it. Where a stage doc and the architecture disagree, the architecture wins and the stage doc is wrong.

---

## How to use a stage document

Every stage doc has the same shape:

| Section              | What it is for                                                                                |
| -------------------- | --------------------------------------------------------------------------------------------- |
| **Outcome**          | what exists at the end, in terms of what a person can do or see                               |
| **Inherits**         | what earlier stages already built — the mirror of their _Hands off_                           |
| **Scope · In / Out** | deliverables, and what a reader might expect here but that lives elsewhere                    |
| **Data**             | migrations this stage owns                                                                    |
| **API surface**      | endpoints and who may call them — contract only                                               |
| **Jobs**             | background work: trigger, idempotency key, failure behaviour                                  |
| **UI**               | screens, and their empty / loading / error / denied states                                    |
| **Offline**          | what every surface does with no network — local, stale, or unavailable, and how that is shown |
| **Acceptance**       | statements that pass or fail on a live demo                                                   |
| **Hands off**        | what the next stage may assume                                                                |
| **Gated on**         | open questions from architecture.md §11 that block this stage                                 |

**Offline is Botica's own section** and has no counterpart in the ELOS documents these are modelled on. Every stage answers it, including the stages whose surfaces are online-only — "this is an office screen, it requires the network, and here is what it shows instead" is a complete answer, and a stage that skips the question has not thought about the product's defining constraint.

The docs describe **what** must exist, never **how** to build it. Stack decisions are fixed in architecture.md §9 and are not re-litigated per stage. Everything else — file layout, module boundaries, patterns — belongs to whoever builds the stage.

Two documents cut across all eleven:

- **[`ownership.md`](./ownership.md)** — one owner per table, per disputed column, per behaviour. `architecture.md` §3 describes the model with no owner column, which is enough to design from and not enough to build from. **Where a stage doc and the ledger disagree, the ledger wins.** Read rules 1–9 before writing any migration. Four of them are Botica's own and are the ones most easily violated by accident: **rule 7** (no stage writes `stock_on_hand` — every stock change goes through S3's ledger service), **rule 8** (every table a till writes carries `client_uuid`, `device_id` and both clocks), **rule 9** (a table reaches a device only by being added to S2's sync registry, with a row-count estimate), and **rule 6**'s fourth context, the sync push, which is a pinned transaction like any other.
- **[`design-system.md`](./design-system.md)** — Part A transcribes the tokens the handoff fixes; Part B is the app component layer a handoff never contains, authored to the same standard. It governs the seven surfaces nobody drew (Precios, Sedes, Reportes, settings, sign-in, the compliance vault, provisioning) as much as the four that were drawn. On any visual question the architecture does not settle, it governs.

---

## Dependency graph

```
S0 Skeleton
   │
   ▼
S1 Catalog
   │
   ▼
S2 Sync
   │
   ▼
S3 Inventory
   │
   ▼
S4 Counter
   │
   ├─────────────┬──────────────┬─────────────┐
   ▼             ▼              ▼             ▼
S5 Handoff   S6 Purchasing   S7 Pricing   S8 Assistant
   └─────────────┴──────┬───────┴─────────────┘
                        ▼
                   S9 Dashboard
                        ▼
                  S10 Operations
```

S5, S6, S7 and S8 are independent of each other and all four fan out from **S4**. Every other edge is a hard sequence. S6, S7 and S8 depend on S4 rather than on S3 because a demand forecast, an elasticity estimate and a cross-sell rule all learn from sales — and until the till exists there are none; none of the three depends on S5, so a tenant with no fiscal document configured still gets a correct suggested order. S9 depends on all four.

| Stage                                         | Delivers                                                                                                                                                              | Depends on |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| [S0 — Skeleton](./S0-skeleton.md)           | repo, compose, two Postgres roles, RLS forced, tenant pinning, auth and invitations, tenants/sedes/users/roles, audit log, the component layer, the application shell | —          |
| [S1 — Catalog](./S1-catalog.md)             | items (products **and** services), `invima_registration`, barcodes, laboratorios, categorías, proveedores, clientes, base prices, tax classes, the load tool              | S0         |
| [S2 — Sync](./S2-sync.md) | the offline substrate: devices, the local store, pull/push, idempotency, the sync registry, the conflict path, sync state UI                                          | S1         |
| [S3 — Inventory](./S3-inventory.md)         | the stock ledger, lots and expiry, receiving, transfers, counts, the Existencias screen, negative-stock exceptions                                                    | S2         |
| [S4 — Counter](./S4-counter.md)           | the till: ticket, keyboard and scanner path, turnos and cash close, payments, returns, offline selling end to end                                                     | S3         |
| [S5 — Handoff](./S5-handoff.md)       | the canonical sale document, the delivery queue with exactly-once retry, per-target mappings, the file export, and the failed-delivery work list                                                                    | S4         |
| [S6 — Purchasing](./S6-purchasing.md)               | the sales-history loader, demand forecast, the suggested order, approval and dispatch, receiving                                                                      | S4         |
| [S7 — Pricing](./S7-pricing.md)               | elasticity estimation, price proposals with margin impact, regulated caps, application                                                                                | S4         |
| [S8 — Assistant](./S8-assistant.md)           | symptom extraction, recommendation, stock-bounded suggestions, safety filtering, the local fallback                                                                   | S4         |
| [S9 — Dashboard](./S9-dashboard.md)                   | `daily_metrics` rollups, the Panel screen, per-sede comparison, reports and exports                                                                                | S5, S6, S7, S8 |
| [S10 — Operations](./S10-operations.md)         | compliance checklist and document vault, provisioning, backups and restore drill, observability, the runbook                                                          | S9         |

**S0–S4 is the first sellable thing** — a network's catalog, live multi-sede stock, and a till that sells through a blackout. **S5 is the one that lets a pharmacy run it without changing how they invoice.** **S8 is the one that demos.** **S9 is the one the owner opens on Monday.**

---

## The four screens that already exist

`../handoff/` ships four finished desktop screens at high fidelity, and they are spread across four stages rather than built together:

| Screen                       | Built by    | Note                                                                                                                              |
| ---------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------- |
| **Inventario · Existencias** | S3          | Server-authoritative and server-paginated, as drawn (`1-15 de 4.284`). The `Estado` column is a derivation whose rules S3 fixes   |
| **Mostrador · Venta**        | S4, then S8 | S4 ships the ticket, the search and the sale; S8 fills the assistant column. S4 must say what occupies that space in the meantime |
| **Compras · Orden sugerida** | S6          | The editable `Sugerido` field records the deviation from the model's proposal — that measurement is the point                     |
| **Panel · Resumen de red**   | S9          | Every tile reads `daily_metrics`, not a live aggregate                                                                         |

Seven surfaces in the shell were never drawn — Precios, Sedes, Reportes, settings, sign-in, the compliance vault and provisioning. They are designed from `design-system.md`, and the stage that builds one says so rather than inventing a visual language beside it.

---

## Amendments

Ten decisions taken in `architecture.md` §14 that extend the ELOS inheritance rather than restate it. Stage documents build to them and cite them as `A1`–`A10`. If any is rejected, the owning stage changes and this list is the diff to apply.

|         | Decision                                                                                                                                                                            | Owner        |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| **A1**  | RLS is real only under three conditions: the runtime role owns no tables, every policy table has `FORCE ROW LEVEL SECURITY`, and every context pins the tenant inside a transaction | S0           |
| **A2**  | Sede scoping is query-layer and UI-default, never RLS. The tenant is the security boundary; the sede is a scope                                                                     | S0 · S3      |
| **A3**  | Stock is a ledger, never a counter. `stock_moves` is append-only; `stock_on_hand` is a rebuildable projection                                                                       | S3           |
| **A4**  | What syncs is one sede's operating set. The counter is local-first; the office is server-authoritative and never synced to a browser                                                | S2           |
| **A5**  | Every client-originated write carries `client_uuid`, `device_id` and `occurred_at`; the server dedupes on `(tenant_id, client_uuid)`                                                | S2           |
| **A6**  | **Deferred, 2026-09-01 — not built at v1.** Fiscal numbering would be leased to devices in contiguous blocks so an offline till could number consecutively. Botica issues no fiscal document and allocates no fiscal number (§8), so the design is kept and not built. The consequence today: the sync state `blocked` has no producer | S5           |
| **A7**  | Products and services are one table. `items.tracks_stock` decides whether a sale moves stock; everything else is identical                                                        | S1           |
| **A8**  | The assistant degrades to **modo local** on synced rules. The advisory notice ships inside the component, not as configurable content                                                 | S8           |
| **A9**  | **Botica never transmits to the DIAN itself.** The client's existing invoicing system issues the fiscal document; Botica hands it a complete, canonical sale over that system's API, exactly once, and records whatever comes back. A proveedor tecnológico is one possible target among others, not a special case in the design | S5           |
| **A10** | Distributor readiness is extension points, not a module                                                                                                                             | S0 · S1 · S3 |

---

## What is gated

Open questions from `architecture.md` §11, mapped to the stages they block. **None of them block S0, S1 or S2** — the first three stages can be built today.

| Question                                                                                                                                    | Blocks                             | Why the stage must not be _demoed_ past it                                                                                                                                                                                                                                                                             |
| ------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| §11.1 — which system each client invoices with, and what its API expects — one mapping per target                | **S5**, per client                 | **Not a blocker.** S5 builds the canonical sale document and the delivery regardless, and the first mapping is written against the first client's system. An instance with no target configured is a supported configuration and is the state every demo runs in (§8). What must not happen is anyone promising DIAN transmission, which Botica does not do |
| §11.2 — what the legacy system can export, above all sales history                                                                          | **S1** load tool · **S6** · **S7** quality | **Not a blocker** (architecture §1, *Cold start*). Every model runs parametric on day one, the demo seed fills every screen, and a client who can export nothing is a supported case rather than a degraded one. What the export changes is how fast a forecast becomes good and how early elasticity appears per item — a quality and timing variable. Worth chasing early because it is free accuracy; not worth blocking a pilot for, and the deck's "trains on your history from day one" is true where the history exists and silent where it does not |
| §11.3 — may customer symptom text reach a model provider at all, and what is retained                                                       | **S8**                             | Health data about an identifiable person under Ley 1581. A "no" costs no code — the assistant runs in **modo local** — but it changes what may be demonstrated to a real customer at a real counter                                                                                                                      |
| §11.4 — is a maintained source of CNPMDM regulated price caps available, and does the pilot sell controlled products                        | **S7**                             | `regulated_max_price` exists either way. Whether a proposal engine may raise a price with no cap to check against is a legal question, not a modelling one                                                                                                                                                          |
| §11.5 — the pilot: which network, how many sedes, how many tills, what the connectivity actually is, and which browser is on those machines | **S2** · **S4**                    | Every budget in §4 is measured on that hardware. "Works on the developer's Mac" is not a result, and the offline contract is the one thing that cannot be validated anywhere else                                                                                                                                      |
| §11.6 — which sede goes first, and who at the client owns the catalog cleanup                                                               | all                                | The deck's own rollout is demo → history load → one sede → the network                                                                                                                                                                                                                                                 |

---

## What this inherits from ELOS, and what it does not

The tenancy model, the security posture, the stack, the job runner, the admin surface, the grid contract and this document discipline come from ELOS v2 (`internal/US-2GZOD/dev-elos`) and are settled. They are not re-argued here.

Four things are genuinely new, carry most of the risk, and are where review attention belongs: **the offline contract** (§5 — a till that sells through a blackout and reconciles without being asked), **the stock ledger** (§6 — append-only, because that is what makes offline safe and inventory defensible), **the fiscal handoff** (§8 — handing a complete sale to the client's own invoicing system, exactly once, and staying silent when none is configured), and **the counter assistant's safety rails** (§7 — a product recommendation made to a customer by someone who is not a pharmacist).

One thing ELOS decided that Botica re-decided rather than inherited: **no Redis** (§4). The reasoning is not carried over on authority — the fan-out is larger here, and the case is re-argued on Botica's own numbers, along with the measurements that would change the answer.
