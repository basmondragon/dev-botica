---
type: ledger
doc: botica-v1-stage-ownership
captured: "2026-09-01"
status: authoritative
source: "[[botica-v1-architecture]] §3, §5, §6"
---

# Ownership ledger

One owner per thing. This document exists because eleven stage documents are written against [`../architecture.md`](../architecture.md) §3, and §3 is a description of the model with no owner column — which is enough to design from and not enough to build from.

**Where this ledger and a stage document disagree, this ledger wins** and the stage document is edited. Where this ledger and `architecture.md` disagree on *what a thing is*, the architecture wins; on *who builds it*, the ledger wins.

**Naming.** Every identifier in this system is English — tables, columns, enum values, API paths, code. Spanish is the interface language and appears in UI strings, and in prose where a domain noun has no honest English equivalent (sede, droguería, mostrador, documento equivalente). The two never mix: the screen a cashier calls **Mostrador** is served by `/api/sales`, and a **sede** is a row in `locations`. See architecture.md §3 for the full glossary.

---

## Rules

**1. Whoever creates a table creates its columns**, including columns only later stages write. A column that lands empty and is filled downstream is created by the table's owner, not by its writer.

**2. Create and write are different.** A stage that creates a table does not necessarily write every value in it. The `Writes` column names who does.

**3. A stage that neither creates nor writes a thing reads it**, and lists it under *Inherits*, never under *Data*.

**4. Indexes and check constraints belong to the stage whose read path or invariant needs them**, not to the table's creator. This is the one exception to rule 1: a stage may migrate an index or a constraint onto a table it does not own, and must say which of its own queries or invariants it serves. **An index is created once, by the first stage that needs it, and every later stage inherits it** — a second stage migrating the same columns onto the same table is a duplicate that fails on a clean build, so a stage whose read path wants an index another stage already declares lists it under *Inherits* and migrates nothing. **When two parallel stages need overlapping indexes on the same table, the lower-numbered stage creates the superset and the other inherits it** — S6 and S7 run in parallel and both want an item-grain index on `sale_lines`, so S6 creates `(tenant_id, item_id, sale_id)` and S7 uses it rather than migrating a near-duplicate that costs a second write on every sale line. Three are load-bearing and are named here rather than left to be discovered: S2's delta cursor index on every synced table (`tenant_id, location_id, updated_at, id`); the rollup source index on `sales (tenant_id, location_id, recorded_at)`, **created by S4** because its own office list needs it first and inherited by S9; and the move-history index on `stock_moves (tenant_id, location_id, item_id, recorded_at)`, **created by S3** and inherited by S6 and S9.

**5. `tenants.settings` is one column with one owner per key group.** S0 creates the column and writes exactly one group. Every other group lands empty and is written by the stage that owns it. A stage inheriting `settings` inherits the column, not its own key group — say so in *Inherits*.

   One owner per group does not by itself stop one stage's write erasing another's: a read-modify-write of the whole column does erase it. Every group is written through a single helper that issues one `jsonb_set` per group, leaves every other group as it stands, and raises rather than passing quietly when the `UPDATE` matches no row — under RLS a write against the wrong pin updates nothing silently, and a `200` would tell an owner their setting was saved when it was not. Within a group, last writer wins.

**6. Every context that touches the database pins the tenant — not just HTTP requests.** A1 makes the runtime role a non-owner under `FORCE ROW LEVEL SECURITY`, so an unpinned connection reads and writes **zero rows**. There are five contexts and S0 owns the pinning path for all of them; later stages call it and never invent their own.

   | Context | How the tenant is established | Used by |
   |---|---|---|
   | HTTP request | middleware opens the transaction and issues `SET LOCAL app.tenant_id` from the session | every stage |
   | Management command | the tenant is an explicit required argument; the command pins before doing any work | S1's load tool, S6's history loader, S10's provisioning |
   | Background job | `tenant_id` is part of the job payload; the job opens its transaction and pins before touching anything | S5, S6, S7, S8, S9 |
   | **Sync push** | the device's session resolves tenant *and* location; the batch is applied inside one pinned transaction, and a row naming another tenant is a rejected batch, not a filtered row | S2, and every stage a till writes to |
   | Unauthenticated inbound | the tenant is not known yet — resolved first, pinned second | **Nobody at v1.** S5 polls its target rather than being called back, so no stage ships an unauthenticated inbound endpoint. S0 still owns the path, because the first stage that needs one must not invent a second |

   The fifth needs one deliberate exception: **a single unpinned resolution path** that maps an external key or an identity to a tenant *before* any pin exists. S0 owns it and labels it chain-wide infrastructure. It is the only query in the system permitted to run outside a pin, and it returns a tenant id and nothing else.

**7. No stage writes `stock_on_hand` directly.** Every stock change — a sale, a receipt, a transfer, a count, an adjustment, a return — is an append to `stock_moves` through S3's ledger service, which maintains the projection in the same transaction (A3). A stage that needs stock to move calls that service. A migration or a job that updates a quantity in place is a defect, not a shortcut, and the projection must be rebuildable from the ledger alone at any time.

**8. Every table a till writes carries the client-write quartet**, created by the table's owner and enforced by S2's helper (A5): `client_uuid` (uuid v7, `UNIQUE (tenant_id, client_uuid)`), `device_id`, and both clocks — `occurred_at` from the device, `recorded_at` from the server. The tables are `sales`, `sale_lines`, `payments`, `sale_returns`, `sale_return_lines`, `shifts`, `stock_moves`, `stock_counts`, `stock_count_lines`, `assistant_queries`, `assistant_suggestions`. Every report, rollup and fiscal deadline uses `recorded_at`; `occurred_at` is displayed to the operator and never used for accounting.

   **A till may also write a table outside that list, and one does.** `customers` carries no `client_uuid` — it is S1's master-data table, not an event log — and a cashier still has to be able to register a customer at an offline counter, because the sale document S5 hands to the client's invoicing system must identify the acquirer by name and identification number, and a till that could not name one would hand over a document with a hole in it. Such a table is pushed under a **declared natural key** instead of the quartet: `customers` dedupes on `(tenant_id, document_type, document)`, and the push returns `applied`, `duplicate` or `merged` rather than failing. A stage that needs a till to write any other master-data table declares its natural key here first; a table with neither a `client_uuid` nor a declared natural key is not pushable, and the push endpoint rejects it.

**9. A table is syncable only if it is in S2's registry.** The set of tables that reach a device, and the predicate that scopes each one to a location, is one declared registry owned by S2 (A4). A stage that needs its table on the till amends that registry, in S2's document, with a stated row-count estimate per location. This is the guard against a 20-location network quietly syncing itself into a browser — the failure this architecture is shaped to avoid, and one that would not be noticed until a pilot till took ninety seconds to start.

---

## Tables

| Table | Creates | Writes | Notes |
|---|---|---|---|
| `tenants` | **S0** | S0, and every stage owning a `settings` group | see the settings register below |
| `locations` | **S0** | S0, S10 | a **sede**. `type` admits `distribution_center` from day one (A10); nothing in v1 creates one |
| `users` | **S0** | S0 | includes `location_id` nullable — null means all locations (A2) |
| `invitations` | **S0** | S0 | invite-only user creation; no self-signup path exists |
| `audit_log` | **S0** | S0 ships the write path; **every stage with an elevated-role mutation writes through it** | append-only. Not an enumerable list — a property each stage honours on its own mutating endpoints |
| `items` | **S1** | S1 | one table for products and services (A7) |
| `item_barcodes` | **S1** | S1 | several per item is normal, not an anomaly |
| `manufacturers` | **S1** | S1 | the **laboratorio** — Genfar, Tecnoquímicas, MK, La Santé, Procaps, Bayer, Baxter in the handoff data. Not a laboratory in the testing sense |
| `categories` | **S1** | S1 | |
| `suppliers` | **S1** | S1, S6 | S6 writes `lead_time_days` back from observed receiving lead time |
| `supplier_items` | **S1** | S1, S6 | S6 updates `cost` from what a receipt actually cost |
| `item_prices` | **S1** | S1, S7 | **S1 writes every price.** S7 writes `regulated_max_price` and nothing else — a cap is a constraint, not a price (A11). There is no `model` source: a model's number reaches this table only by a person typing or confirming it, and the row is then `manual` and carries their name |
| `customers` | **S1** | S1, S4 | S1 creates and loads; **S4 is the only interactive writer** — a customer is created at the counter, and **offline**, which rule 8's second paragraph makes safe. S2 carries that write but does not author it |
| `imports` | **S1** | S1, S6 | S1's master-data loader and S6's sales-history loader both record runs here |
| `devices` | **S2** | S2 | S2 alone. With numbering leases deferred (A6) no other stage reads or writes this table |
| `sync_conflicts` | **S2** | S2, S3, S4 | S2 creates and writes protocol-level rejections; **S3 writes the negative-stock rows**; **S4 writes `stale_price` and `catalog_divergence`**, the two reconciliations that arrive with an offline sale (§5). All three of §5's named reconciliations therefore have a writer, which they did not when this ledger was first written |
| `lots` | **S3** | S3, S6 | a **lote**. S6 creates lots on receiving and writes `unit_cost` |
| `stock_moves` | **S3** | **S3 only** | append-only. Every other stage moves stock by calling S3's service (rule 7). The `document_type`/`document_id` pair is how a move names the sale, order, transfer or count that caused it |
| `stock_on_hand` | **S3** | **S3 only** | projection, maintained in the same transaction as the moves; rebuildable |
| `stock_policies` | **S3** | S3, S6 | `min_quantity`, `max_quantity`, `reorder_point`, `target_coverage_days`, `source`. **S3 writes `manual`; S6 writes `model`.** This is what lets S3's Existencias screen show the `Punto de reorden` state three stages before a forecast exists |
| `transfers` · `transfer_lines` | **S3** | S3 | |
| `stock_counts` · `stock_count_lines` | **S3** | S3 | a count writes an adjusting move, never an overwrite (§6) |
| `shifts` | **S4** | S4 | a **turno**: open with a float, sell, close with a declared count |
| `sales` | **S4** | S4, S6 | S6 writes `imported` history rows — see disputed columns |
| `sale_lines` | **S4** | S4, S6, S8 | S8 writes `from_suggestion`; **S6 writes the lines of the `imported` history rows** it loads (§11.2) — see disputed columns |
| `payments` | **S4** | S4 | |
| `sale_returns` · `sale_return_lines` | **S4** | S4 | the credit note they cause is S5's |
| ~~`dian_resolutions`~~ | — | — | **Not built at v1** (A6, deferred). Botica allocates no fiscal numbers because it issues no fiscal documents (§8) |
| ~~`numbering_leases`~~ | — | — | **Not built at v1** (A6, deferred). Kept as a design in S5's *Gated on* for the day Botica issues |
| `fiscal_documents` | **S5** | S5 | one row per handoff of a sale or return to the client's invoicing system. **No rows at all while no target is configured** (§8), which is the default and is not an error state. `document_key` is what makes redelivery safe — the single place in the product where a duplicate is a tax problem rather than a bug (§8) |
| `purchase_orders` · `purchase_order_lines` | **S6** | S6 | |
| `goods_receipts` · `goods_receipt_lines` | **S6** | S6 | receiving creates `lots` and calls S3's ledger service — it does not write `stock_moves` itself (rule 7) |
| `demand_forecasts` | **S6** | S6 | read by S3's screen and S9's tiles |
| `elasticity_estimates` | **S7** | S7 | |
| `price_proposals` | **S7** | S7, S1 | S7 computes and supersedes them; **S1 resolves one** when a person acts on it in the price editor, stamping `taken`, `modified` or `dismissed` with `resolved_by` and `resolved_price`. Acting on a suggestion is a write to `item_prices`, and that write is S1's (A11) |
| `assistant_queries` | **S8** | S8 | |
| `assistant_suggestions` | **S8** | S8 | `accepted` and `sale_line_id` are written when the cashier presses `Agregar` |
| `cross_sell_rules` | **S8** | S8 | mined from the tenant's own sales; synced to the till (A8) |
| `item_warnings` | **S8** | S8 | the safety layer. Loaded with the catalog, editable by `owner`/`admin` |
| `report_schedules` | **S9** | S9 | `report_id`, parameters JSONB, `cadence`, `delivery_hour`, `format`, recipients, `next_run_at`, `last_run_at`, `last_run_status`. Added to this register after the fact: S9 specified the jobs, the endpoints and the UI for scheduled reports against a table no stage created, which is a hole rule 1 exists to prevent |
| `daily_metrics` | **S9** | S9 | the rollup that makes the Panel fast at 20 locations (§4) |
| `compliance_documents` | **S10** | S10 | the vault. **Not** where S5 keeps its handoff artefacts — the file exports and any target-returned PDF URL hang off `fiscal_documents` |
| `checklist_templates` · `checklist_entries` | **S10** | S10 | |

### Enums

| Enum | Creates | Values |
|---|---|---|
| `role` | S0 | `platform_admin`, `owner`, `admin`, `cashier` |
| `location_type` | S0 | `store`, `warehouse`, `distribution_center` (A10) |
| `item_type` | S1 | `product`, `service` (A7) |
| `vat_class` | S1 | `excluded`, `exempt`, `rate_5`, `rate_19` |
| `invima_status` | S1 | `valid`, `in_process`, `expired`, `not_applicable` |
| `price_source` | S1 | `manual`, `imported`. **No `model` value exists** (A11): a model never writes a price, so there is no source to record it under. A price a person set after reading a suggestion is `manual` and carries `proposal_id` |
| `stock_move_type` | S3 | **S3 creates the full enum; the causing stage is fixed per value below** |
| `sync_conflict_type` | S2 | **S2 declares every value at creation**, including `negative_stock` (written by S3) and `stale_price` and `catalog_divergence` (written by S4). No later stage runs `ALTER TYPE` — a value added by the stage that writes it is a migration that must land before the stage that reads it, which is a coordination bug waiting for a clean build |
| `sale_status` | S4 | `open`, `closed`, `voided` |
| `sale_source` | S4 | `counter`, `imported` — S4 writes the first, S6 the second |
| `fiscal_document_status` | S5 | `pending`, `sent`, `acknowledged`, `failed`. Not DIAN states — these describe **our handoff**, not the receiving system's filing (§8) |
| `purchase_order_status` | S6 | `suggested`, `approved`, `sent`, `partially_received`, `received`, `discarded` |
| `price_proposal_status` | S7 | `proposed`, `above_cap`, `taken`, `modified`, `dismissed`, `superseded`. **S7 creates the enum and writes the first two and the last; S1 writes `taken`, `modified` and `dismissed`** when a person acts in the price editor (A11). The sharpest cross-stage case in the register: the stage that computes a suggestion is not the stage that records what became of it, and that separation is the whole point |
| `suggestion_type` | S8 | `first_choice`, `conditional`, `bought_together` — rendered as `Primera opción`, `Con condición`, `Se lleva junto` |

### `stock_moves.type` — every value, and who causes it

`stock_moves` is append-only and is the source of every quantity in the product, so a row written with the wrong `type` is not correctable — it is only reversible by another row. Five stages cause moves; each value's cause is fixed here, not chosen at build time. In every case the row is written **by S3's ledger service** (rule 7); the stage named is the one whose action causes it.

| `type` | Sign | Caused by | When |
|---|---|---|---|
| `receipt` | + | **S6** | a goods receipt against a purchase order is confirmed |
| `sale` | − | **S4** | a sale closes, one row per line whose item has `tracks_stock` |
| `customer_return` | + | **S4** | a customer returns a unit |
| `supplier_return` | − | **S6** | stock is returned to a supplier |
| `transfer_out` | − | **S3** | a transfer is dispatched from the origin location |
| `transfer_in` | + | **S3** | a transfer is received at the destination location |
| `adjustment` | ± | **S3** | a manual correction with a stated reason |
| `shrinkage` | − | **S3** | breakage, loss or theft |
| `expiry` | − | **S3** | an expired lot is written off |
| `count` | ± | **S3** | a cycle count reconciles to the physical shelf |

A transfer is two rows, not one, and they are written at different times — dispatch and receipt — because stock in transit belongs to neither location's shelf and pretending otherwise is how transfers lose merchandise.

---

## Disputed columns

Every one of these was claimable by more than one stage, or by none.

| Column | Creates | Writes | Resolution |
|---|---|---|---|
| `items.tracks_stock` | **S1** | S1 | The switch that makes a service a service (A7). S3 reads it to decide whether a sale moves stock; S4 reads it to decide whether a line needs a lot. Neither writes it |
| `items.invima_registration`, `invima_expires_at`, `invima_status` | **S1** | S1 | The **registro INVIMA**, an explicit product requirement and not a custom field (§3). S3's grid filters on it, S10's checklist reads its expiry, neither writes it. Nothing validates it against INVIMA's register (§12) |
| `items.custom` | **S1** | S1, S6, S7 | JSONB for what a specific droguería tracks and the model does not. S6 and S7 read it as model features and may write derived bands, each under its own key |
| `item_prices.proposal_id`, `set_by_user_id` | **S1** | S1 | Created and written by the table's owner. `proposal_id` is nullable and names the suggestion a person acted on, which is what lets S7 measure whether its own output is trusted without ever writing a price itself (A11). `set_by_user_id` answers the question every price dispute starts with |
| `items.regulated_max_price`, `cap_status` | **S1** | **S7** | Created empty by the table's owner. Only S7 populates a cap, and only S7 checks a proposal against one (§11.4). A null cap means *unknown*, never *uncapped* — S7 says which |
| `stock_policies.reorder_point` | **S3** | S3, S6 | The `Punto de reorden` state on the Existencias screen must work at S3, three stages before a forecast exists. S3 writes a manual threshold; S6 overwrites with the model's and sets `source = model`. `source` is what stops S6 silently erasing a threshold a pharmacist set on purpose |
| `lots.unit_cost` | **S3** | S3, S6 | S3 creates lots for opening stock and adjustments; S6 writes the cost a goods receipt actually paid |
| `sale_lines.unit_cost` | **S4** | S4 | Stamped from the lot at the moment of sale. Margin computed later by joining to a lot's *current* cost is wrong the first time a cost changes, and every margin figure on the Panel depends on this being stamped rather than derived |
| `sale_lines.from_suggestion` | **S4** | **S8** | Created by the table's owner, written by the assistant when `Agregar` is pressed on a suggestion card. This single column is what makes the Panel's *"58,6% de sugerencias aceptadas"* tile answerable; without it the number is a guess |
| `sale_lines.lot_id` | **S4** | S4 | Nullable for services and for items whose `tracks_lots` is false. FEFO by default, cashier override recorded (§6) |
| `sales.number` | **S4** | S4 | The internal, per-location sale number, allocated locally and always present. **It is not a fiscal number** — Botica allocates none (§8, A6). Where the client's invoicing system returns its own, it lands in `fiscal_documents.external_number`, and `sales.number` is the key the two systems reconcile on. Conflating an internal sale number with a fiscal one is the most likely modelling error in the product |
| `sales.source` | **S4** | S4, **S6** | `counter` \| `imported`. S6's history loader writes `imported` rows so the forecast and the elasticity model have something to learn from on day one (§11.2). Every rollup, KPI and fiscal query filters on this — an imported sale from the previous system was never fiscally issued by us and must never appear in `fiscal_documents`, in a shift, or in a cash reconciliation |
| `sales.status` → `voided` | **S4** | S4 | Voiding a sale is S4's. The credit note it requires is S5's, and a sale voided after its handoff reached `acknowledged` is a credit note through the same path, never a deletion |
| `sales.client_uuid`, `device_id`, `occurred_at`, `recorded_at` | **S4** | S4 | The client-write quartet (rule 8). The convention and the dedupe helper are S2's; the columns belong to the table's owner |
| `stock_moves.client_uuid`, `device_id` | **S3** | S3 | Same rule. A move caused by an offline sale carries the till's identity, which is how a discrepancy is traced to a device and a shift |
| `shifts.declared_total`, `variance` | **S4** | S4 | The cash count at close. `variance` is stored, never suppressed — a till that hides its shortfalls is a till nobody reconciles |
| `fiscal_documents.cude`, `external_number` | **S5** | S5 | Whatever the receiving system returns, where it returns anything. Null is normal and is not a failure — the handoff succeeding is what `acknowledged` records. **Never generated locally** (A9) |
| `fiscal_documents.document_key` | **S5** | S5 | Derived from the sale, stable across every retry, and the far end's dedupe key. A timeout that is retried must never produce a second invoice at the target — this is the one delivery in the product where at-least-once is not good enough (§8) |
| `demand_forecasts.coverage_days` | **S6** | S6 | Read by S3's Existencias screen for the `Sobrestock` state and by S6's own `Cobertura` column. The colour thresholds live in the design system, not in the model |
| `purchase_order_lines.suggested_quantity` vs `approved_quantity` | **S6** | S6 | Two columns, never one. The difference is the only honest measure of whether the model is trusted, and overwriting the suggestion destroys it permanently (§3) |
| `cross_sell_rules` on the device | **S8** | S8 | Added to S2's sync registry by S8, under rule 9, with a stated per-location row cap. This is what the assistant falls back to offline (A8) |
| `item_warnings` on the device | **S8** | S8 | Same. The safety layer must be local, or a blackout removes the safety rather than the network |
| `daily_metrics.*` | **S9** | S9 | Recomputed idempotently per location per day. A backfill that runs twice produces the same rows, because a rollup that cannot be re-run is a rollup nobody dares fix |
| `checklist_entries.document_id` | **S10** | S10 | The link from "we say we did this" to the PDF that shows it. Nullable — some checklist items have no document |

---

## `tenants.settings` — the key register

One column, one owner per key group, written through the helper in rule 5.

| Group | Owner | Carries |
|---|---|---|
| `/api/settings/tenant` | **S0** | identity, NIT, timezone, currency and number formatting |
| `/api/settings/sync` | **S2** | pull interval, local retention window, batch sizes, the storage-persistence policy |
| `/api/settings/inventory` | **S3** | expiry alert horizons (the Panel's 90-day tile reads this), FEFO override policy, negative-stock behaviour, count cadence |
| `/api/settings/invoicing` | **S5** | the target system, the field mapping in use, retry policy, and whether delivery is per-sale or batched. **The credential is never here** — it lives in the instance's secrets store, keyed per tenant, because a credential in a JSONB column is a credential every `admin` query can read. **Empty is the default and means the handoff is off** — no rows, no queue, no errors, no Panel tile (§8) |
| `/api/settings/purchasing` | **S6** | default lead times, coverage targets, order caps, model refresh cadence |
| `/api/settings/pricing` | **S7** | margin goal (the Panel's `≥ 22%` reference), maximum single-step price change, whether uncapped items may be raised |
| `/api/settings/assistant` | **S8** | kill switch, per-tenant spend cap, model, whether transcripts are retained, and for how long (§11.3) |
| `/api/settings/compliance` | **S10** | which checklist template applies, expiry warning horizon, notification recipients |

Every write runs behind the single permission dependency: `owner`, and `admin` for everything except billing and API-key settings. The tenant is never addressed by id — it is pinned per request, so a settings write always targets the current tenant, and a tenant id in the path is either dead weight or a write outside the pin (rule 6).

---

## Cross-stage services

Eight pieces of behaviour are used by stages that do not own them. Each has one implementation, named here so nobody writes a second.

| Service | Owner | Everyone else |
|---|---|---|
| **Tenant pinning** — HTTP, command, job, sync push, unauthenticated inbound | **S0** | calls it. Never issues its own `SET LOCAL` |
| **Location scoping** — the shared predicate that confines a `cashier` and defaults the office to all locations | **S0** | calls it. Never re-implements the rule (A2) |
| **The stock ledger** — append a move, maintain the projection, enforce lot and sign rules | **S3** | calls it. Never writes `stock_moves` or `stock_on_hand` (rule 7) |
| **Idempotent client writes** — dedupe on `(tenant_id, client_uuid)`, apply a batch in one transaction, report per-row outcomes | **S2** | calls it. Never dedupes by hand (rule 8) |
| **The audit write path** — actor, action, entity, before/after | **S0** | calls it on every elevated-role mutation |
| **The demo seed** — one command building a synthetic tenant that looks like the handoff (§1) | **S1** creates the command and seeds the catalog | every later stage registers its own fixtures with it and is not finished until its screens render from the seed. Synthetic only — never loaded into a real tenant |
| **The sale handoff service** — build the canonical document from a closed sale or return, enqueue it for delivery under a stable `document_key`, record the outcome | **S5** | called by S4's sale-close and return paths. No stage builds its own payload or calls a target directly (§8, A9) |
| **The model gateway client** — one OpenRouter path with the per-tenant cap, the kill switch and the cost log | **S8** | called by S6 for purchase-order reason text. Whichever of the two lands first builds it; the owner is S8 either way |

A stage that finds itself needing a ninth adds it here first, and says which stage owns it.
