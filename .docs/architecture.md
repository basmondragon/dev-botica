---
type: architecture
doc: botica-v1-architecture
company: "[[particula-tech]]"
product: "Botica"
market: "Colombia"
captured: "2026-09-01"
status: authoritative
source: "brief.pdf; handoff/README.md; [[elos-v2-architecture]]"
---

# Botica — Architecture

The operating platform for Colombian droguerías: inventory, purchasing, pricing, the counter, and the management view, on one database.

This document is the authority. The stage documents in [`stages/`](./stages/) decompose it; they do not extend it. Where a stage document and this document disagree, this document wins and the stage document is wrong. Where this document and [`stages/ownership.md`](./stages/ownership.md) disagree about who creates a table or a column, the ledger wins — it is the only document written with all stages in view.

---

## 0. What this inherits, and what it changes

Botica is built on the ELOS v2 architecture (`internal/US-2GZOD/dev-elos/.docs/architecture.md`), which is in production and settled. The tenancy model, the security posture, the stack, the job runner, the admin surface, the grid contract and the stage-document discipline are inherited wholesale and are not re-litigated here. What follows is the diff.

| | ELOS | Botica |
|---|---|---|
| Users | sales reps and their managers, at desks, online | cashiers at a counter and owners in an office — two different read models on one database (§4) |
| Network | assumed present; a failed request is an error | assumed absent several times a week; a failed request is a normal state the till sells through (§5) |
| The hot path | an approval queue a human reads at their own pace | a sale, mid-queue, with a customer waiting. Every millisecond is visible to a stranger (§6) |
| Truth | rows in Postgres, read on demand | rows in Postgres, with a scoped replica on each till that must never disagree with them in a way that loses money (§5) |
| The regulated surface | none — email deliverability at worst | a fiscal document per sale, which v1 models and hands to the client's own issuing system rather than transmitting itself (§8) |
| Multi-location | one tenant, one workspace | one tenant, many **sedes**, each with its own stock, its own till, its own numbering (§2) |
| The AI layer | drafts outbound email a human approves | recommends a product to a customer standing in front of a person who is not a pharmacist (§7) |

Three things are genuinely new and carry most of the risk: the offline contract (§5), the stock ledger (§6), and the counter assistant's safety rails (§7). A fourth, the fiscal handoff (§8), looks like risk and is deliberately not — it was scoped down to handing a complete sale to the system the client already invoices with, precisely so it would stop being on this list. Everything else is ELOS with a different domain.

---

## 1. What the system does

A droguería network runs on Botica. Merchandise arrives and is received against a purchase order, by barcode, lot and expiry date. Every sale at every counter moves that stock in the same instant, across the whole network, with no nightly close. When one sede runs out of something another has too much of, the transfer is a few clicks rather than a phone call. A model that has learned each product's rotation per sede proposes what to buy, how much, and when — and says why, per line. A second model measures how much each product's demand actually moves when its price does, and proposes the price changes that raise the month's margin without selling another unit. At the counter, an assistant reads what the customer said, and suggests what the sede has in stock right now, with the reason for each suggestion and a hard rule against pretending to be a doctor. The owner sees the whole network — margin per sede, rotation per category, stock-outs, expiring inventory — without asking anyone for a report.

The deck states the market this is aimed at: roughly 38.000 droguerías in Colombia, of which about 30.500 are independent points competing against the large chains, on desktop software written fifteen years ago, spreadsheets, and the owner's memory. Botica is not a bespoke build per customer. It is a finished platform configured with a customer's catalog and sedes: demo on the client's catalog, load the sales history, pilot one sede, then the network.

### The four screens that exist

`handoff/` ships four finished desktop screens at high fidelity, and they anchor the build. **Panel** (network summary — KPIs, daily sales, per-sede sales, assistant acceptance, per-sede table), **Inventario / Existencias** (the master stock table by product, laboratory, sede, lot and expiry), **Mostrador** (assisted sale — the customer's symptom, the assistant's recommendation, suggestions bounded by that sede's stock, and the ticket in progress), and **Compras — Orden sugerida** (the model's replenishment order, with editable quantities). Their tokens, densities and component specifications are transcribed into [`stages/design-system.md`](./stages/design-system.md), which governs every surface, including the ones the handoff does not draw.

The interface is Spanish (Colombia) throughout: COP with a thousands dot (`$15.600`), decimals with a comma (`24,8%`), millions abbreviated `$9,4 M`, dates `MM/AAAA`. This is a product decision, not a localisation layer — there is no second locale in v1 and no `i18n` runtime. Code, tables, columns and API paths are English, without exception; the interface is Spanish, without exception. §3 carries the glossary that maps the handful of domain nouns living on both sides of that line.

---

### Cold start — every model works on day one

**A client's sales history is an accelerant, never a precondition.** The deck promises the models train on the droguería's own history from day one, and where that export exists they do. Where it does not — which is the demo, the pilot's first week, and any client whose fifteen-year-old system exports nothing usable — **the product still works, the screens still fill, and the models still produce output.** A platform that needs a data migration before it can be shown is a platform that cannot be sold.

Each model therefore runs in one of three regimes, and **always says which one it is in**:

| Regime | When | What it uses |
|---|---|---|
| **Parametric** | day one, no history at all | the pharmacist's own parameters — `stock_policies` min/max/reorder point, the margin goal, the coverage target — plus category-level defaults that are stated as assumptions rather than presented as findings |
| **Learning** | Botica's own sales are accumulating | its own `sales` rows, with the parametric floor still holding where the signal is too thin |
| **Learned** | enough signal, whether imported or earned | the full estimate, with its window and observation count |

**The regime is visible, not hidden.** Every model row carries the basis it was computed from and a confidence, and the surfaces show it — the handoff already reserves the affordance, drawing `Confianza del modelo` as a filter chip on the Compras screen, and that chip is exactly this. An administrator must be able to sort the suggested order by how much the model actually knows, because the honest answer on day one is *not much, and here is what I used instead*.

**A model with no basis withholds rather than guesses**, and the two cases differ in kind. A demand forecast degrades gracefully: with no history it falls back to the sede's own reorder points, which a pharmacist set deliberately and which are better than a guess. An elasticity does not degrade at all — a price that has never moved yields no estimate of what happens when it moves, and inventing one would be the single most damaging thing this product could do to a customer's margin. So S7 ships **two engines**: a margin rule that needs no history and works on day one, and elasticity that appears per item as that item earns it. The Precios screen is useful immediately and honest immediately, which is a better outcome than a screen that waits ninety days to say anything.

**The demo seed.** One command, **owned by S0** because S0 creates the first seedable tables and its own verification needs a tenant to run against, builds a tenant that looks like the handoff — Droguerías La 45, its six sedes, a catalog with lots and expiry dates, stock, and enough synthetic sales for every screen to render as drawn. Each stage contributes the fixtures for its own tables, and **a stage is not finished until its screens render convincingly from the seed**, which is a sharper completion test than a passing test suite: it catches the empty state nobody designed and the tile whose denominator is zero. The seed is synthetic and self-evidently so; it is never a template for a real tenant, and it is never loaded into one.

**The seed has named profiles, and they are part of the contract.** Several stages need to verify behaviour a full, healthy tenant cannot show — a comparator that withholds because there is no previous period, an engine that has no sales to fit, a screen at a scale the pilot has not reached. Those states are reached by a **declared profile**, never by hand-editing a database, because a check that begins "first, delete some rows" is a check nobody runs twice. The set is fixed here and S0's command implements it; each stage's fixture responds to the profile it is given.

| Profile | What it builds | Who needs it |
|---|---|---|
| `default` | Droguerías La 45 — six sedes, 180 days of sales, Usme deliberately cold | every stage |
| `young` | the same network with twelve days of history | S9's withheld comparators, the states most likely met on a real first day |
| `cold` | catalog, stock and no sales at all | S6's parametric order, S7's sales-free path, S8's empty rule set |
| `scale` | twenty locations, enough rows to measure against | S9's and S10's budget checks, which cannot be made on six |
| `minimal` | one network, one sede, one owner | S0's isolation checks, which need a second tenant to be isolated *from* |

**A check asserts against the seed's own counts, never against production sizing.** The registry figures in S2 and the row counts in S3 are what a real pilot will hold; the seed holds what its fixtures build. A check that expects nine thousand customers because the sizing table says so will fail on every run, and the fix is to assert against the seed's numbers or against what the product itself reports.

---

## 2. Tenancy, sedes and roles

**A tenant is a droguería network.** One legal entity, one NIT, one catalog, one price policy, many sedes. `Droguerías La 45` with six sedes in Bogotá is one tenant. Two networks on one instance never see each other's rows, and that is enforced by Postgres rather than by the application remembering to filter.

**A sede is a physical location** — a point of sale, a warehouse, or later a distribution centre. Stock is held per sede. Tills belong to a sede. Purchase orders, transfers and stock counts are all scoped to one. A sede is not a tenant: the network's catalog, its suppliers, its price lists and its people are shared, and the whole commercial point of the product is seeing the network as one thing rather than as six islands.

**Tenant isolation is RLS. Sede scoping is not.** Every table carrying `tenant_id` has a row-level security policy `USING (tenant_id = current_setting('app.tenant_id')::uuid)` with `FORCE ROW LEVEL SECURITY`, and the Django runtime role owns no tables (A1). Sede visibility is a query-layer rule and a UI default, never RLS (A2). The reason is the same one ELOS gives for rep scoping: an owner comparing six sedes needs to read all six in one query, and a policy that made that impossible would be worked around within a week. A cashier's inability to see another sede's till is clarity and least privilege, not a security boundary — the security boundary is the tenant.

### Roles

Four roles, one enum, one permission table, checked by a single dependency.

| Role | Is | May |
|---|---|---|
| `platform_admin` | us | reach any tenant by pinning one at a time, through Django admin. Belongs to no tenant |
| `owner` | the network's owner | everything in the tenant: users, roles, hard delete, price approval, purchase-order approval, settings, exports |
| `admin` | the administrator or regente running the network day to day | `owner` minus hard delete, role changes, and billing/API-key settings. May archive. This is Marcela Ríos in the handoff |
| `cashier` | the person at the counter, the **cajero** | their own sede: sell, return, open and close their turno, look up stock across the network read-only, log a manual movement. No pricing, no purchasing, no user administration. This is Andrés Peña in the handoff |

No policy engine, no per-object ACLs. A `cashier`'s home sede is `users.location_id`; `owner` and `admin` see every sede and default to all of them. `platform_admin` reaches a tenant only by selecting it, never by querying across tenants.

The handoff's sidebar is role-gated on exactly this: Panel, Inventario, Compras, Precios, Mostrador, Sedes and Reportes for `owner` and `admin`; Mostrador and a read-only Inventario for `cashier`.

---

## 3. Domain model

Every business table carries `id` (uuid), `tenant_id`, `created_at`, `updated_at`, an RLS policy and `FORCE ROW LEVEL SECURITY`. Only the columns that carry a decision are listed. [`stages/ownership.md`](./stages/ownership.md) assigns every table and every disputed column to exactly one creating stage — this section describes the model, the ledger says who builds it.

### Naming, and the one glossary that matters

**Every identifier is English. Every interface string is Spanish.** Tables, columns, enum values, API paths and code are English, because a build agent, a migration and a stack trace are read by engineers. The product a cashier sees is entirely Spanish (Colombia), because that is who uses it. The two never mix and are never machine-translated into each other: the screen a cashier calls **Mostrador** is served by `/api/sales`, and what an owner calls a **sede** is a row in `locations`.

Five domain nouns appear constantly in prose and in the interface, and each maps to exactly one identifier. Getting this table wrong is how a codebase ends up with `location_id` next to `location_id` meaning the same thing.

| Spanish (interface, prose) | English (identifier) | What it is |
|---|---|---|
| sede | `locations` | a physical branch — a shop floor, a warehouse, later a distribution centre |
| mostrador | `sales`, and the counter surfaces | the counter, and by extension the act of selling at it |
| lote | `lots` | a manufacturing batch, carrying its own expiry, supplier and cost |
| turno | `shifts` | a cashier's cash session, from opening float to declared close |
| laboratorio | `manufacturers` | the pharmaceutical manufacturer — Genfar, Tecnoquímicas, MK, Procaps. Not a testing laboratory |
| documento equivalente | `fiscal_documents` | the electronic POS document the DIAN requires per sale |
| registro INVIMA | `items.invima_registration` | the sanitary registration a product is legally sold under |

### Identity and structure

| Table | Carries |
|---|---|
| `tenants` | `name`, `slug`, `nit`, `status`, `settings` JSONB (one key group per owning stage) |
| `locations` | `code`, `name`, `type` (`store` \| `warehouse` \| `distribution_center`), `address`, `city`, `phone`, `status`. The `type` enum is what makes a distributor a configuration rather than a migration (A10) |
| `users` | `email`, `name`, `role`, `tenant_id` nullable (null for `platform_admin`), `location_id` nullable (home sede; null = all locations), `status`, `last_login_at`, `platform_admin` |
| `invitations` | invite-only user creation. There is no self-signup path |
| `audit_log` | `actor_user_id`, `action`, `entity_type`, `entity_id`, `before`, `after`. Append-only |
| `devices` | a browser install that sells: `location_id`, `label`, `device_key`, `last_seen_at`, `last_synced_at`. The unit of sync and of blame. It carries no fiscal role — Botica allocates no fiscal numbers (§8, A6) |

### Catalog — products and services in one table

The catalog holds **everything a location can put on a ticket**, and that includes things with no stock: toma de presión, inyectología, glucometría, asesoría farmacéutica, domicilio. Services are not a second system bolted alongside products; they are rows in `items` with `tracks_stock = false` (A7). A ticket line does not care which it is, the fiscal document does not care, and the margin report treats a service as a product with no cost of goods unless one is entered.

| Table | Carries |
|---|---|
| `items` | `type` (`product` \| `service`), `name`, `description`, `manufacturer_id`, `category_id`, `presentation`, `active_ingredient`, `strength`, **`invima_registration`**, `invima_expires_at`, `invima_status` (`valid` \| `in_process` \| `expired` \| `not_applicable`), `requires_prescription`, `controlled`, `cold_chain`, `unit`, `splittable` + `units_per_pack`, `vat_class`, `tracks_stock`, `tracks_lots`, `tracks_expiry`, `active`, **`regulated_max_price` nullable**, `cap_status`, `custom` JSONB. **The cap lives on the item, not on a price row** — it is a regulatory property of the product set by the CNPMDM, and putting it on `item_prices` would mean every new price row had to carry the previous one's cap forward, with the guardrail failing silently on exactly the reference somebody just repriced (§11.4, A11) |
| `item_barcodes` | `item_id`, `code`, `is_primary`. A product routinely carries several — the manufacturer's EAN, the distributor's, and one the droguería printed itself |
| `manufacturers` | `name`, `nit`. The **laboratorio** |
| `categories` | `name`, `parent_id`. Flat enough to filter, nested enough to roll up |
| `suppliers` | `nit`, `name`, `contact`, `payment_terms`, `lead_time_days`. Coopidrogas in the handoff data |
| `supplier_items` | `supplier_id`, `item_id`, `supplier_code`, `cost`, `min_order_pack` |
| `item_prices` | `item_id`, `location_id` nullable (null = network-wide), `price`, `effective_from`, `effective_to`, `source` (`manual` \| `imported`), `proposal_id` nullable, `set_by_user_id`. **There is no `model` source** — no model writes a price (A11) |
| `customers` | `document_type`, `document`, `name`, `phone`, `email`, `address`, `data_consent`, `notes` |
| `imports` | one row per run of a load tool |

**`invima_registration` is a first-class column, not a custom field.** It is the sanitary registration a product is legally sold under, it expires on its own schedule independent of any lot's expiry, and a droguería is inspected against it. It is filterable in the inventory grid, it appears on the product detail, and an item whose registration is `expired` is flagged wherever it is sold. What Botica does *not* do is decide the consequence: it surfaces the state and records the pharmacy's own decision. Validating a registration against INVIMA's register is not in v1 (§12).

**`vat_class`** is one of `excluded`, `exempt`, `rate_5`, `rate_19`. The great majority of medicines are excluded from IVA under article 424 of the Estatuto Tributario (tariff headings 30.03 and 30.04), while a large share of what a droguería actually sells — cosmetics, toiletries, drinks, devices — is not. A single tax rate per ticket is wrong on the first ticket, so the class is per item and the tax is computed per line.

### Inventory — a ledger, not a counter

| Table | Carries |
|---|---|
| `lots` | `item_id`, `lot_code`, `expires_at`, `supplier_id`, `unit_cost`, `invima_registration` nullable |
| `stock_moves` | **append-only.** `location_id`, `item_id`, `lot_id` nullable, `quantity` signed in base units, `type`, `document_type` + `document_id`, `unit_cost`, `occurred_at` (the till's clock), `recorded_at` (the server's), `device_id`, `user_id`, `client_uuid` |
| `stock_on_hand` | the projection: `location_id`, `item_id`, `lot_id`, `quantity`, `updated_at`. Derived, rebuildable, never the source of truth |
| `stock_policies` | `item_id`, `location_id`, `min_quantity`, `max_quantity`, `reorder_point`, `target_coverage_days`, `source`. What the stock-state column reads before a forecast exists |
| `transfers` + `transfer_lines` | inter-location transfer with a state machine: `draft` → `dispatched` → `received` \| `partial` |
| `stock_counts` + `stock_count_lines` | cycle counts. A count does not overwrite a quantity — it writes the adjusting move that reconciles it |

`stock_moves.type` is `receipt`, `sale`, `customer_return`, `supplier_return`, `transfer_out`, `transfer_in`, `adjustment`, `shrinkage`, `expiry`, `count`. §6 explains why this is a ledger.

### The counter

| Table | Carries |
|---|---|
| `shifts` | `location_id`, `user_id`, `opened_at`, `closed_at`, `opening_float`, `declared_total`, `variance`, `status` |
| `sales` | `location_id`, `shift_id`, `number`, `status` (`open` \| `closed` \| `voided`), `source` (`counter` \| `imported`), `customer_id` nullable, `subtotal`, `discount`, `tax`, `total`, `sold_by_user_id`, `device_id`, `client_uuid`, `occurred_at`, `recorded_at` |
| `sale_lines` | `item_id`, `lot_id` nullable, `quantity`, `unit_price`, `discount`, `vat_class`, `tax_amount`, `unit_cost`, `from_suggestion` |
| `payments` | `sale_id`, `method` (`cash` \| `debit_card` \| `credit_card` \| `transfer` \| `other`), `amount`, `reference` |
| `sale_returns` + `sale_return_lines` | a return against a closed sale, whole or partial |

`sale_lines.from_suggestion` is what makes the Panel's *"58,6% of assistant suggestions accepted"* tile answerable. Without it the number is a guess, and a guess is worse than no tile.

### Fiscal

One table, because Botica hands a sale over and records what happened to it (§8). It does not number, sign or transmit anything.

| Table | Carries |
|---|---|
| `fiscal_documents` | `sale_id` \| `sale_return_id`, `document_key` (the stable id that makes redelivery safe), `target` (which system it was sent to), `payload` JSONB (the canonical document exactly as sent), `status` (`pending` \| `sent` \| `acknowledged` \| `failed`), `attempts`, `sent_at`, `acknowledged_at`, `external_number` and `cude` where the target returns them, `pdf_url`, `response` JSONB, `error` |

`dian_resolutions` and `numbering_leases` are **not built at v1** and appear in no migration (A6, deferred). They are the shape fiscal numbering would take if Botica ever issued, and S5 keeps the design in its *Gated on* rather than in its *Data*.

### Purchasing and pricing

| Table | Carries |
|---|---|
| `purchase_orders` | `location_id`, `supplier_id`, `status` (`suggested` \| `approved` \| `sent` \| `partially_received` \| `received` \| `discarded`), `source` (`model` \| `manual`), `model_version`, `total`, `approved_by`, `sent_at` |
| `purchase_order_lines` | `item_id`, `suggested_quantity`, `approved_quantity`, `received_quantity`, `unit_cost`, `reason`, `confidence`, `coverage_days` |
| `goods_receipts` + `goods_receipt_lines` | receiving against an order; creates `lots` and stock moves |
| `demand_forecasts` | `item_id`, `location_id`, `weekly_sales`, `trend`, `coverage_days`, `reorder_point`, `safety_stock`, `computed_at`, `model_version` |
| `elasticity_estimates` | `item_id`, `location_id` nullable, `elasticity`, `r2`, `window`, `observations`, `computed_at`, `model_version` |
| `price_proposals` | `item_id`, `current_price`, `suggested_price`, `current_margin`, `projected_margin`, `estimated_monthly_impact`, `status` (`proposed` \| `above_cap` \| `taken` \| `modified` \| `dismissed` \| `superseded`), `respects_regulated_cap`, `resolved_by`, `resolved_price`. An analysis, never an instruction (A11) |

`purchase_order_lines.suggested_quantity` and `approved_quantity` are two columns on purpose. The handoff's Compras screen lets the administrator edit the model's number, and the difference between the two is the only honest measure of whether the model is trusted. Overwriting the suggestion destroys that measurement permanently.

### The assistant

| Table | Carries |
|---|---|
| `assistant_queries` | `location_id`, `sale_id` nullable, `transcript`, `symptoms` JSONB, `recommendation`, `mode` (`model` \| `local`), `model`, `cost_usd`, `latency_ms` |
| `assistant_suggestions` | `query_id`, `item_id`, `type` (`first_choice` \| `conditional` \| `bought_together`), `reason`, `price`, `accepted`, `sale_line_id` nullable |
| `cross_sell_rules` | `item_a_id`, `item_b_id`, `support`, `confidence`, `window`, `computed_at`. Mined from the network's own sales; synced to the till as the offline fallback (A8) |
| `item_warnings` | `item_id`, `type` (`interaction` \| `contraindication` \| `do_not_suggest_if`), `text`, `severity`, `source` |

### Reporting and compliance

| Table | Carries |
|---|---|
| `daily_metrics` | `location_id`, `date`, `revenue`, `tickets`, `average_ticket`, `margin`, `cost`, `stockouts`, `units`, `suggestions_offered`, `suggestions_accepted`. The rollup that makes the Panel fast at 20 locations (§4) |
| `compliance_documents` | the vault: `location_id` nullable, `type`, `name`, `file_url`, `expires_at`, `uploaded_by`, `notes` |
| `checklist_templates` | the list a droguería is expected to keep: `code`, `title`, `description`, `requires_document`, `frequency` |
| `checklist_entries` | `location_id`, `template_id`, `status` (`pending` \| `done` \| `not_applicable`), `document_id` nullable, `verified_by`, `verified_at`, `expires_at` |

Compliance in v1 is a checklist and a place to keep the PDFs, with expiry dates that raise a flag. Botica records what the pharmacy says it has done and holds the evidence. It does not file anything with anyone, does not compute regulatory reports, and does not certify compliance (§12).

---

## 4. Two read models on one database

This is the section that decides whether the product feels fast, and it is the one place Botica's shape genuinely departs from ELOS.

**The counter and the office read the same database in opposite ways.**

| | Mostrador (the till) | Gestión (the office) |
|---|---|---|
| Who | `cashier` — the **cajero** — standing, customer waiting | `owner` / `admin`, seated |
| Scope | **one sede's operating set** — its catalog, prices, stock, cross-sell rules, its own open tickets and its own day | the whole network, any period, any sede |
| Where the data is | in the browser, durably, before the screen is opened | in Postgres, fetched per view |
| Offline | **fully operational** — sells, prints, queues | unavailable, and says so plainly |
| Read latency | 0ms — it is a local query | one request, server-paginated |
| Freshness | its own sede within seconds; other sedes' stock as of last sync, labelled | live per request |
| Volume on the wire | a delta every few seconds, usually empty | one page of rows per interaction |

A chain of 20 sedes must never sync 20 sedes into a browser. That is the mistake that would make this slow, and the boundary above is what prevents it: **what syncs is one sede's operating set, and nothing else** (A4). A till holds a few megabytes. An owner looking at 20 sedes is doing an aggregate read, and aggregate reads are what a rollup table is for.

### On Redis

**No Redis. Postgres holds the queue, the pub/sub, the locks and the cache**, exactly as ELOS §7 decided, and the reasoning is stronger here rather than weaker.

The two loads worth naming: writes are trivial (20 sedes × 600 tickets/day ≈ 12.000 tickets, roughly 40 row-writes per second at peak — Postgres 18 does not notice this); reads are a fan-out of "anything new?" from every connected till. That second one is real, and it has two answers before a second datastore is one of them. Delta pulls are a single indexed cursor query per device per interval that usually returns zero rows and costs about a millisecond, and tills only need *fresh*, not *live* — the counter reads its own sede from local storage at zero latency regardless. When the poll cost stops being free, the next step is `LISTEN/NOTIFY` behind SSE so a device pulls only when something changed for its sede, which is a Postgres feature and not a service.

And the specific reason a cache is the wrong shape *here*: a cached stock level is a number that can be wrong on a screen a cashier is about to sell from. Cache invalidation on inventory converts a correctness problem into a latency optimisation, and this product was commissioned because the correctness problem is what droguerías have today.

**The measurement that would change this answer**, so it stays an engineering decision rather than a belief: sustained delta-pull load above ~500 requests/second on one instance, or p95 latency on the delta endpoint above 50ms, or Panel queries that a rollup table cannot flatten. The first two are answered by SSE over `LISTEN/NOTIFY`; only if that is insufficient does Redis pub/sub become the cheapest remaining option, and it would arrive as a fan-out bus, never as a copy of inventory. The sync module is a boundary precisely so this can change without touching the domain (§5).

### Performance budget

The deck's promise is speed, and the handoff draws a dense screen. These are the numbers the build is held to, measured on the pilot's hardware — not aspirations.

| Path | Budget |
|---|---|
| Keystroke to filtered product list at the counter | **< 30ms** p95, over the local store, with no network in the path |
| Barcode scan to line added to the ticket | **< 50ms** p95 |
| `Cobrar` pressed to the ticket being closed and the receipt on screen | **< 200ms** p95, offline included. Fiscal transmission is asynchronous and never blocks it |
| Counter app cold start, warm cache, to sellable | **< 2.5s** |
| Panel first paint at 20 sedes, 30-day period | **< 1.2s** p95, served from `daily_metrics` |
| Inventory grid page, any filter combination | **< 400ms** p95 server time, server-paginated |
| Delta pull, no changes pending | **< 20ms** p95 server time |

The first three are non-negotiable and are what "must be fast" means. They are achievable only because the counter never asks the network for anything on the critical path — which is the offline contract, and not a coincidence.

---

## 5. The offline contract

A droguería sells when the internet is down. This is not a degraded mode to be apologised for; on the pilot's connectivity it is a weekly event, and a till that stops selling is a till that gets replaced by a paper notebook and a calculator. Google Docs is the reference the client named, and it is the right one: work continues, the document is never lost, and it reconciles when the connection returns without anyone being asked what to do about it.

### What runs where

The counter is a browser tab. There is no desktop application, no Windows installer, and nothing for a pharmacy to install — a deliberate constraint, and the reason the whole local layer lives inside Chrome's own storage.

- **Local store:** RxDB (Apache-2.0 core) over IndexedDB. Schemas, reactive queries, and a replication protocol that persists its own checkpoint, resumes after an arbitrary offline stretch, retries with backoff, and hands conflicts to a handler we write.
- **Sync transport:** RxDB's generic replication against **our own Django endpoints** — `GET /api/sync/pull` with a cursor, `POST /api/sync/push` with a batch. No sync service, no logical replication, no second datastore. The stack stays four containers and one Postgres.
- **Durability:** `navigator.storage.persist()` is requested at first run and its state is displayed. An unsynced sale living in evictable storage is an unacceptable risk, and a device that refuses persistence is a device the operator must be told about.
- **Multi-tab:** RxDB `multiInstance` over `BroadcastChannel`; two tabs on one till share one store and one replication.

**Why not a sync engine.** PowerSync and ElectricSQL both solve this well and both run in a browser. Each adds a service to operate and a second replication path into Postgres, against an architecture whose defining choice is that Postgres does everything (§4, ELOS §7). The part of the problem that is genuinely hard — checkpointing, resumption, retry, batching, conflict callbacks — is what RxDB's protocol provides under Apache-2.0. The part that is genuinely ours — what a sede is allowed to pull, what a push is allowed to do, and what happens when a sale was made against stock that no longer exists — is domain logic that cannot be delegated to any engine. Adding a service to acquire the first half while still writing the second is a bad trade at this size. **This is a bounded decision:** all sync code lives behind `core/sync/` on the server and one client module, and no domain code calls the replication API directly. If the measurements in §4 ever justify an engine, it replaces that module.

### The five rules

**1. The till never invents truth.** Its local store is a *snapshot of server state* plus *its own pending events*. It never computes a stock level and calls it authoritative. Where a local figure could differ from the server's — because a pull is outstanding or a push is queued — the UI says so, in the words §B.9 of the design system fixes, rather than showing a confident number that is wrong.

**2. Selling never blocks on the network, and never blocks on stock.** A sale is a physical fact that already happened: the customer is holding the box. A till that refuses a sale because its stock snapshot says zero is a till that is wrong about the world and is also losing the money. The sale is recorded, and stock going negative is an **exception raised to the office**, never a refusal at the counter. §6 explains why the ledger makes this safe.

**3. Every client-originated write is idempotent by construction.** Each carries a client-generated `client_uuid` (v7, so it sorts by time), a `device_id`, and `occurred_at` from the till's clock. The server dedupes on `(tenant_id, client_uuid)` with a unique index. A push that times out after the server committed is retried and is a no-op. This is the property that makes an unreliable network safe, and it is why the `client_uuid` column is on every table a till writes rather than only on `sales`.

**4. The clock is not trusted, and is not discarded.** `occurred_at` is the till's wall clock and is what the cashier will swear to. `recorded_at` is the server's and is what every report, every rollup and every fiscal deadline uses. Both are stored, always. Skew beyond a threshold is surfaced on the device rather than silently corrected, because a till whose clock is two days out is a till whose operator needs to know.

**5. Pushes carry events, not row states.** The till does not push "stock is now 14". It pushes "this sale happened, with these lines". The server derives every consequence — the moves, the projection, the fiscal document, the metrics. This is what makes two tills selling the same last box a reconcilable event rather than a lost update, and it is why the whole write path is append-only.

### What the operator sees

Sync state is never hidden. The handoff already draws its resting form — *"Sincronizado hace 4 s"* in the filter bar — and the states behind it are: **synced** (nothing pending), **pending** (n operations queued, with the count), **offline** (no connection, selling normally, n queued), **degraded** (selling normally, but something needs an operator — pushes failing for a stated reason, or the browser having refused persistent storage), and **blocked** (the till cannot safely continue). **At v1 nothing produces `blocked`** — it existed for an exhausted fiscal numbering lease, and Botica no longer allocates fiscal numbers (§8, A6). The state stays defined because the day a till must stop is the day nobody wants to be designing the message.

**`blocked` means "stopping is safer than continuing", and at v1 nothing qualifies.** A browser that refuses `navigator.storage.persist()` is `degraded`, not `blocked`: the data is at risk of eviction, which is worth a one-time dialog the operator must dismiss and a persistent chip, but refusing to sell over a risk is worse than the risk, and a shop that cannot sell has a certain loss instead of a possible one. The state is reserved for a condition where continuing would produce something invalid rather than merely degraded — which, with fiscal numbering out of scope, no longer exists in the product.

### Conflicts, and the ones that are not conflicts

Because the write path is an append-only event log with idempotency keys, the classic conflict — two writers, one row, one loses — does not arise for sales and movements. Two tills selling simultaneously produce two events; both are true; the ledger sums them. What remains are three real reconciliations, each with a decided answer: **oversell** (the sum of events drives a lot below zero — both sales stand, a `sync_conflicts` row is raised to the office naming the sede, item, lot and the two sales, and the counting screen is where it is resolved), **stale price** (a price changed while a till was offline — the sale stands at the price actually charged, `sale_lines.unit_price` is the record, and the difference is reported rather than corrected), and **catalog divergence** (an item was deactivated while a till was offline — the sale stands, and the item is flagged on arrival). In all three the counter's version of events is preserved, because it is the one that matches what physically happened in the shop.

---

## 6. Inventory is a ledger

**No code updates a stock quantity in place.** Stock is the sum of an append-only `stock_moves` table; `stock_on_hand` is a projection maintained inside the same transaction as the moves that change it, and it can be dropped and rebuilt from the ledger at any time without loss (A3). This is the single most important structural decision in the product, and it is the one that makes every other requirement possible.

It is what makes offline safe: appends from several tills commute, so events arriving late, out of order, or twice cannot corrupt the total. It is what makes the numbers defensible: every unit is traceable to the document that moved it, the person who did it, the device it happened on and the moment it was recorded, which is what a droguería's owner cannot get today and what an inspection asks for. It is what makes cycle counts honest, because a count writes an adjusting move with a reason instead of quietly overwriting a number and erasing the discrepancy that was the point of counting. And it is what makes cost of goods computable, because the moves carry the cost at which each unit actually moved.

**Lots are not optional bookkeeping.** A pharmaceutical unit is identified by item *and* lot: the expiry date, the supplier, the acquisition cost and any sanitary alert all attach to the lot. The Inventario screen's `Vence` column and the Panel's *"inventario por vencer · 90 días · $18,9 M · 142 lotes"* tile are both lot-grain, and a recall — INVIMA withdrawing a lot from the market — is answerable only if every sale records which lot went out the door. Consumption defaults to FEFO (first expired, first out) and the cashier can override when the physical box in their hand says otherwise; the override is recorded rather than prevented.

**Services move nothing.** An item with `items.tracks_stock = false` writes no `stock_moves` row when sold. Everything else about it — price, tax class, margin, its line on the ticket, its place in the fiscal document — is identical to a product. This is the whole cost of supporting services, and it is why they are the same table (A7).

---

## 7. The counter assistant

The cashier types or dictates what the customer said. The assistant answers **over the stock that sede physically has right now**, with a stated reason per suggestion. The handoff's Mostrador screen is the contract: a recommendation in plain language, then suggestion cards labelled `Primera opción`, `Con condición` or `Se lleva junto`, each carrying the units available at that sede and why it is being offered, each with an `Agregar` button that puts it on the ticket flagged as suggestion-originated.

**Four layers of context**, in the ELOS shape: what the customer said (the transcription and the symptoms extracted from it); what this sede has (live local stock, price, and lot expiry); what this network's customers actually buy together (`cross_sell_rules`, mined from the tenant's own sales, not from a general prior); and the safety layer (`item_warnings` — interactions, contraindications, and conditions under which a product must not be suggested, such as the handoff's *"no ofrecer si la fiebre pasa de 38,5 °C o si hay sangre"*).

**The safety rails are structural, not editorial.**

- The advisory notice — *"Con fiebre de más de dos días, remitir a consulta médica. Botica no diagnostica."* — ships inside the suggestion component itself, not as content a deployment can configure away. The handoff says it is mandatory; making it a property of the component is what makes that true in six months.
- A product carrying a blocking `item_warning` for the stated symptoms is **not** suggested. Filtering happens before ranking, never after, and never as a warning bolted onto a suggestion that was already made.
- `items.requires_prescription` items are never suggested by the assistant. They can be sold, by a person who has seen the prescription.
- The assistant suggests; it never diagnoses, never names a condition, and never contradicts a prescription. This is a prompt-level constraint *and* an output check, because a prompt-level constraint alone is a hope.

**It degrades, it does not disappear** (A8). The recommendation is a model call and needs the network. The suggestions do not: `cross_sell_rules` and `item_warnings` are synced to the till, so with no connection the assistant still offers what this sede's customers buy together, still filters on the safety layer, and labels itself `modo local` so nobody mistakes it for the full thing. A counter tool that goes blank when the wifi drops teaches the staff to stop using it.

**Symptom text is health data.** Under Ley 1581 it is sensitive personal data, and it is entered about an identifiable person standing at the counter. What is retained, for how long, and whether it may reach a model provider at all is §11.3 — a gate, and one that must be answered before the assistant is demonstrated on real customers rather than after.

---

## 8. The fiscal handoff

**Scope decision, 2026-09-01: Botica does not issue fiscal documents and does not talk to the DIAN.** It is the system of record for the operation — the catalog, the stock, the sale, the purchase, the price. The fiscal document is produced by whatever system the client already uses, and every one of those systems has an API. Botica's whole responsibility is to **hand over a complete and correct sale, reliably, exactly once.** That is one deliverable, not a compliance programme, and it is the entire content of this section.

The obligation is real and it is the pharmacy's. Since 2024 every POS receipt in Colombia is an electronic document — the *documento equivalente electrónico*, governed by Resolución DIAN 000165 de 2023 and its Anexo Técnico DEE 1.0, modified by Resolución 000202 de 2025 and compiled into Resolución Única 000227 de 2025 — carrying a CUDE, transmitted to the DIAN, identifying the acquirer by name and identification number. A droguería trading today already meets that obligation somehow. Botica does not replace how, and does not assume it.

### One document, one delivery, any target

The design is a canonical payload plus a pluggable transport. There are no modes and no per-vendor branches in the domain.

1. **The canonical sale document.** One complete representation of a closed sale, and of a return as a credit note referencing it. It carries the emitter (the tenant's NIT and the sede), the acquirer (document type, number, name — captured at the counter, offline included), every line (item code, description, quantity, unit price, discount, `vat_class` and the computed tax amount per line), the totals, the payment methods, both timestamps, and Botica's internal `sales.number` as the stable external key. **Completeness is the whole game**: a field the receiving system needs and we did not send is a field a cashier re-types, and a handoff that gets re-typed is worse than no handoff.
2. **Delivery, with a queue behind it.** An HTTP POST to a configured endpoint, retried with backoff, recorded per attempt. Where a client's system has no API, the same payload drops as a file export on a schedule. Where a client wants a specific proveedor tecnológico, that is one more target with one more mapping — not a different architecture.
3. **A mapping per target.** The canonical payload is ours; the field names are theirs. Each target gets a declarative mapping, and adding a target is a mapping plus credentials, not a change to the sale.
4. **Exactly once, under retry.** Every document carries a stable id derived from the sale, so a retry after a timeout cannot produce a second invoice at the far end. **This is the one place where getting it wrong is a tax problem rather than a bug** — a duplicated fiscal document is harder to unwind than a missing one, so the delivery is built to be safely re-runnable from the first line of code.
5. **Whatever comes back is recorded.** Where a target returns its own number, a CUDE, a status or a PDF URL, it is stored on `fiscal_documents` and shown on the sale. Where it returns nothing, the row still records that the handoff succeeded, because the office's real question is *"is anything stuck?"*.

When a target *is* configured, `fiscal_documents.status` moves `pending` → `sent` → `acknowledged`, with `failed` for a delivery the target rejected. A retry job drains the queue; failures land on a work list an administrator resolves, never on the cashier. **The sale never waits for any of it** — the handoff is asynchronous, off the counter's critical path, and a till with no connection queues the document alongside the sale itself (§5).

### Unconfigured is not an error

**With no target configured, the handoff does not exist.** This is the default state — a demo, a pilot's first week, a client who has not yet given us their API — and it is a normal, supported configuration rather than a broken one.

Concretely, and these are binding: no `fiscal_documents` rows are written; no delivery is queued and no retry job runs; no failed-delivery work list appears; the Panel shows no unsent-documents figure — **the tile is absent, not zero**; and nothing anywhere reports an error, a warning, or a badge about it. A cashier sells, the customer gets a receipt, and the subject never comes up. The only place the state is visible at all is the settings screen that would configure it, which says plainly that no invoicing system is connected.

This costs nothing to support because **the canonical document is a derivation of the sale, not a stored artifact.** It is built when it is about to be sent. So an instance that runs unconfigured for a month loses nothing: connect a target later and any sale in the database can be rendered into a document and delivered on demand, without a backfill mechanism existing for it. Whether anyone wants that is a client's decision — a pharmacy that has been invoicing in their own system all month will not want a month of duplicates, which is precisely why nothing is queued in the first place.

The general rule this is an instance of, and later stages should follow it: **an integration that is not configured is off, not failing.** Silence is the correct behaviour for a capability nobody has asked for yet, and a product that nags about unconfigured optional integrations trains its users to ignore its warnings.

### What this decision removes from v1

Stated plainly, because these were in an earlier draft and their absence is deliberate rather than an oversight.

- **No numbering leases, and no `dian_resolutions`** (A6, deferred). If the client's system issues, the client's system numbers. The lease mechanism — a contiguous block of a sede's range issued per device so an offline till could number consecutively without colliding — is kept in S5's *Gated on* for the day Botica issues, and is not built now. One consequence travels with it: the sync state `blocked` loses its only producer, so at v1 it is defined and never raised (§5).
- **No CUDE generation, no XML signing, no DIAN annexes, no proveedor tecnológico contract.** Botica is not a PT and does not become one (A9).
- **No claim that Botica makes anyone compliant.** It hands over accurate data. What the receiving system does with it, and whether the customer gets the fiscal document at the counter or later, is the client's flow and the client's decision.

The one thing this section does insist on: the customer leaves with a receipt from Botica carrying the internal sale number, and that number is the key both systems share. Without a shared key, reconciling a day of sales against a day of invoices is manual, and it will be done at month end by someone who did not make the sale.

---

## 9. Stack and services

Inherited from ELOS §7, verified 2026-08-10, plus the client-side layer that ELOS has no equivalent of. Four containers, one database, no Redis (§4).

| Layer | Pick |
|---|---|
| API | **Django 6.1 + django-ninja 1.5.x** |
| DB | **Postgres 18**, shared schema + `tenant_id` + RLS + `FORCE ROW LEVEL SECURITY` |
| Pooling | Django native psycopg3 pool (`OPTIONS: {"pool": True}`). No pgbouncer |
| Jobs | **Procrastinate 3.9** — Postgres-backed queue, cron, retries, locks |
| Auth | **django-allauth headless** + our own `Invitation` model. Invite-only |
| Internal admin | **Django admin** — the platform-admin surface |
| Frontend | **Vite 8.1 + React 19 + TanStack Router + Query v5 + Table v9 + shadcn/ui + Tailwind v4** |
| API client | `openapi-typescript` + `openapi-fetch`, generated from `/api/openapi.json` |
| Local store | **RxDB (Apache-2.0 core) over IndexedDB (Dexie storage)**, `multiInstance`, persisted storage requested |
| Sync | RxDB generic replication against our own `/api/sync/*` endpoints. No sync service |
| Offline shell | Service worker (Workbox) for the application shell only. **The service worker never caches API responses** — the local store is the offline data layer, and two caches would be two truths |
| Fiscal handoff | The client's own invoicing system over HTTPS, behind one target interface and one mapping per target. **No vendor is contracted at v1**, and an instance with no target configured is a supported configuration (§8) |
| Model gateway | OpenRouter, zero-data-retention routing, per-tenant caps and kill switch |
| Proxy | Caddy — TLS, auto-renew |
| Deploy | Docker Compose via Coolify on Hetzner |

**Services:** `web` (Django + Ninja, serving the built React assets), `worker` (same image, `procrastinate worker`), `postgres`, `caddy`. Same image for web and worker.

The rules that bite if ignored, carried over: never `SET search_path` per tenant — that is schema-per-tenant in disguise; `SET LOCAL app.tenant_id` is transaction-scoped and pooler-safe, so tenant pinning depends on request-level transactions being on; `LISTEN` does not work behind a transaction-mode pooler.

**The grid contract**, for every server-authoritative surface: server pagination from day one — `manualPagination`, `manualSorting`, `manualFiltering`, `rowCount` from the API — with filter and sort state in TanStack Router's typed search params, so any view is a link. The handoff's Inventario screen is drawn to this contract (`1-15 de 4.284`, `Filas 25`, pages `1 · 2 · 3 · … · 172`). Counter surfaces do not use it: they query the local store.

---

## 10. External vendors

| Vendor | For | Where | Failure behaviour |
|---|---|---|---|
| **The client's own invoicing system** — whatever they already run | receives the canonical sale document over its API and issues the fiscal document itself | S5 | the sale still closes; the handoff sits `pending` and drains on retry. Never on the counter's critical path (§8) |
| **OpenRouter** | the counter assistant's recommendation and symptom extraction; reason text on purchase suggestions | S6, S8 | the assistant falls back to `modo local` on synced rules (§7); purchasing shows quantities without prose |
| **Object storage** (S3-compatible) | compliance documents, and the handoff's file exports plus any PDF a target returns a URL for | S5, S10 | uploads retry; the vault is read-only while unreachable. Botica generates no fiscal XML and signs nothing (§8) |
| **Email** (transactional) | invitations, expiry alerts, scheduled reports | S0 | queued and retried |

No vendor sits on the counter's critical path. That is a hard rule, and it is what the offline contract is for.

---

## 11. Confirm before building

Open questions, and the stage each one blocks. None of them block S0, S1 or S2.

| | Question | Blocks | Why it matters |
|---|---|---|---|
| **11.1** | **Which system each client invoices with, and what its API expects** — one mapping per target | **S5**, per client | Not a blocker. S5 builds the canonical document and the delivery regardless, and the first mapping is written against the first client's system. What must not happen is anyone promising DIAN transmission, which Botica does not do (§8) |
| **11.2** | **What the legacy system can export** — catalog, stock, lots, suppliers, and above all sales history | **S6** and **S7** quality, **S1**'s load tool mapping | **Not a blocker** (§1, *Cold start*). Every model runs parametric on day one and the demo seed fills every screen, so the product is demonstrable and pilotable with no export at all. What the export changes is how fast the forecast becomes good and how early elasticity appears per item — a quality and timing variable, not a gate. Worth chasing early because it is free accuracy; not worth blocking a pilot for |
| **11.3** | **May customer symptom text reach a model provider at all**, and what is retained | **S8** | Health data about an identifiable person under Ley 1581. A "no" costs no code — the assistant runs in `modo local` — but it changes what is demonstrated and what is sold |
| **11.4** | **Regulated maximum prices** (CNPMDM circulars): is a maintained source of price caps available, and does the pilot sell products under control | **S7** | `items.regulated_max_price` exists either way, and it now matters more than it did: A11 stops the model writing prices, but a person acting on a margin-rule suggestion can still raise one on day one, and an unknown cap is the only thing between that and a regulated breach. A null cap means *unknown*, never *uncapped*. |
| **11.5** | **The pilot**: which network, how many sedes, how many tills per sede, what the connectivity is actually like, and what browser is on those machines | **S2**, **S4** | Every budget in §4 is measured on that hardware. "Works on the developer's Mac" is not a result |
| **11.6** | **Which sede goes first**, and who at the client owns the data cleanup | all | The deck's own rollout is demo → history load → one sede → the network. The catalog cleanup is the client's work and is usually the schedule risk |

---

## 12. Deliberately not building

Named so nobody rediscovers them as gaps at a demo.

**No native or desktop application.** The counter is a browser tab. No installer, no Windows build, no per-machine deployment (§5).

**No hardware integration in v1.** No ESC/POS thermal printing, no cash-drawer kick, no serial scale. A barcode scanner in HID keyboard mode needs no integration and is assumed. Receipts are on screen, as PDF, and by QR. Any of these becoming a requirement means a local agent per counter, and that is a new stage with a new stack decision, not a patch.

**Botica is not a proveedor tecnológico**, does not implement DIAN's technical annexes, does not generate a CUDE, does not sign an XML and does not transmit to the DIAN. It hands a complete sale to the system that does (§8, A9).

**No regulatory reporting engine.** Compliance in v1 is a checklist, a document vault and expiry flags (§3). Botica does not compute or file the monthly controlled-medicine consumption report, does not maintain the libro de control as a legal instrument, and does not certify anything. Where a droguería must report, Botica holds the records that make it possible and says so plainly.

**No INVIMA register lookup.** `invima_registration` is captured, displayed and flagged on expiry. It is not validated against INVIMA's online register.

**No EPS or convenio dispensing** — authorizations, copays, per-payer price lists. A real revenue line for many droguerías and a materially different ticket flow. Not in v1.

**No delivery / domicilios**, no e-commerce storefront, no WhatsApp ordering.

**No accounting, no payroll, no nómina electrónica, no electronic payroll document.** Botica exports what an accountant needs and stops there.

**No CRM, no loyalty programme, no patient clinical record.** `customers` exists to identify the acquirer on a fiscal document and to recognise a returning customer. It is not a medical history.

**No dark theme, no white-label theming, no SSO, no custom-fields UI, no per-object permissions, no workflow builder.**

**No distributor module** — but the extension points are deliberate (A10): `locations.type` already admits `distribution_center`, purchase orders and transfers are already documents between locations, and price lists are already scoped. Serving distributors is a later product decision, and the schema will not have to be unpicked to make it.

---

## 13. Build stages

Eleven stages. [`stages/README.md`](./stages/README.md) is the index and holds the dependency graph; each document is scoped so a build agent can work from it plus this document plus the ledger, without reading the other ten.

| Stage | Delivers | Depends on |
|---|---|---|
| **S0 — Skeleton** | repo, compose, migrations, two Postgres roles, RLS, tenant pinning, auth and invitations, tenants/sedes/users/roles, audit log, the design-system component layer, the application shell | — |
| **S1 — Catalog** | items (products **and** services), `invima_registration`, barcodes, laboratorios, categorías, proveedores, clientes, base prices, tax classes, the internal load tool, the catalog grid | S0 |
| **S2 — Sync** | the offline substrate: devices, the local store, pull/push endpoints, idempotency, the conflict path, sync state UI, persisted storage | S1 |
| **S3 — Inventory** | the stock ledger, lots and expiry, receiving, transfers, cycle counts, the Existencias screen, negative-stock exceptions | S2 |
| **S4 — Counter** | the till: ticket, keyboard and scanner path, turnos and cash close, payments, returns, offline selling end to end | S3 |
| **S5 — Handoff** | the canonical sale document, the delivery queue with exactly-once retry, per-target mappings, the file export, and the work list for failed deliveries | S4 |
| **S6 — Purchasing** | the sales-history loader, demand forecast per item per sede, the suggested order, approval and dispatch, receiving against an order, the Orden sugerida screen | S4 |
| **S7 — Pricing** | elasticity estimation, the pricing analytics surface, suggested prices with margin impact, regulated caps. It writes no price (A11) | S4 |
| **S8 — Assistant** | symptom extraction, recommendation, stock-bounded suggestions, safety filtering, the local fallback, acceptance metrics | S4 |
| **S9 — Dashboard** | `daily_metrics` rollups, the Panel screen, per-location comparison, scheduled reports and exports | S5, S6, S7, S8 |
| **S10 — Operations** | compliance checklist and document vault, tenant and sede provisioning, backups and restore drill, observability, the operator runbook | S9 |

S5, S6, S7 and S8 are independent of each other and all four fan out from S4. Every other edge is a hard sequence. S6, S7 and S8 depend on S4 rather than on S3 because a forecast, an elasticity estimate and a cross-sell rule all learn from sales — and until the till exists there are none; none of the three depends on S5, so a tenant with no fiscal document configured still gets a correct suggested order. S9 depends on all four, because the Panel reads the rollup, the forecast, the margin goal, the assistant's acceptance figures and S5's unsent-document count. The history loader that gives them something to learn from on day one belongs to S6 and writes `sales` rows marked `imported`.

Every stage contributes to the demo seed described in §1, and a stage is not finished until its own screens render convincingly from it.

**S0–S4 is the first sellable thing**: a network's catalog, live multi-sede stock, and a till that sells through a blackout. **S8 is the one that demos.** **S9 is the one the owner opens on Monday.** **S5 is the one that lets a real pharmacy run it without changing how they invoice** — a deliberately small claim, and the reason it is small is §8.

---

## 14. Amendments

Decisions taken in writing this document that extend rather than restate the ELOS inheritance. Stage documents cite them as `A1`–`A11`. If one is rejected, the owning stage changes and this list is the diff to apply.

| | Decision | Owner |
|---|---|---|
| **A1** | **RLS is real only under three conditions**, inherited from ELOS A1 and restated because everything rests on it: the Django runtime role owns no tables, every policy table has `FORCE ROW LEVEL SECURITY`, and every context that touches the database pins the tenant inside a transaction — HTTP requests, management commands, background jobs, and the sync push endpoint alike | S0 |
| **A2** | **Sede scoping is query-layer and UI-default, never RLS.** The tenant is the security boundary; the sede is a scope. An owner comparing six sedes reads all six in one query, and a `cashier` is confined by the query helper and the UI default | S0 · S3 |
| **A3** | **Stock is a ledger, never a counter.** `stock_moves` is append-only; `stock_on_hand` is a projection maintained in the same transaction and rebuildable from the ledger. No code path updates a quantity in place | S3 |
| **A4** | **What syncs is one sede's operating set.** The counter is local-first over its own sede; the office is server-authoritative over the network and is never synced to a browser. Two read models, one database | S2 |
| **A5** | **Every client-originated write carries `client_uuid`, `device_id` and `occurred_at`**, and the server dedupes on `(tenant_id, client_uuid)` with a unique index. Retries are safe by construction, on every table a till writes | S2 |
| **A6** | **Deferred, 2026-09-01 — not built at v1.** Fiscal numbering would be leased to devices in contiguous blocks so an offline till could number consecutively without colliding. Botica no longer issues fiscal documents, so it allocates no fiscal numbers (§8). The design is kept for the day that changes; the consequence today is that the sync state `blocked` has no producer | S5 |
| **A7** | **Products and services are one table.** `items.tracks_stock` decides whether a sale moves stock; everything else — pricing, tax, the ticket line, the fiscal document, margin — is identical | S1 |
| **A8** | **The assistant degrades to `modo local`.** Cross-sell rules and safety warnings are synced to the till, so suggestions and filtering survive a blackout; only the language model's recommendation needs the network. The advisory notice ships inside the component, not as configurable content | S8 |
| **A9** | **Botica never transmits to the DIAN itself.** The client's existing system issues the fiscal document; Botica hands it a complete, canonical sale over that system's API, exactly once, and records whatever comes back. A proveedor tecnológico is one possible target among others, not a special case in the design | S5 |
| **A10** | **Distributor readiness is extension points, not a module.** `locations.type` admits `distribution_center`, transfers and purchase orders are already documents between locations, and price lists are already scoped. No distributor feature ships in v1 | S0 · S1 · S3 |
| **A11** | **The pricing model never writes a price.** Elasticity and the margin rule produce an *analysis* — what a reference earns today, what the data says about its price sensitivity, and a suggested price with the confidence behind it. A price changes only when a person changes it, in the catalog's own price editor, and the row that results is `manual` and carries their name. There is no `model` price source, no scheduled repricing, no bulk apply, and no path by which a model's number reaches a till without a human having typed or confirmed it. `item_prices.proposal_id` records which suggestion informed the change, and `price_proposals.resolved_price` records what the person actually chose — because the gap between the suggestion and the decision is the only honest measure of whether the model is worth trusting, and it is the same measurement `purchase_order_lines.suggested_quantity` and `sale_lines.from_suggestion` exist to preserve | S1 · S7 |
