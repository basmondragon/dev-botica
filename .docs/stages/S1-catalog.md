---
stage: S1
title: Catalog
depends_on: [S0]
blocks: [S2]
source: "architecture.md §1, §2, §3, §4, §5, §8, §9, §11.2, §11.6, §12, §13; amendments A1, A2, A7, A10; stages/ownership.md rules 1–6 and 9, the disputed-column register and the cross-stage services; stages/design-system.md Part A and Part B; ../handoff/README.md"
---

# S1 — Catalog

## Outcome

At the end of this stage a droguería network has a catalog in Botica that its own staff recognise. An administrator opens **Inventario · Catálogo**, types `losartán` or scans a box or pastes an INVIMA registration number, and the row comes back from four thousand in under half a second. Opening it shows what the product is — its laboratorio, its presentación, its principio activo, its concentración — what the law says about it — its registro INVIMA, when that registration expires and what state it is in, whether it needs a prescription, whether it is controlled, whether it needs cold chain — what it costs the customer, at what IVA class, and which suppliers sell it and for how much. An owner can correct any of it and the correction is on the audit trail.

The catalog holds services in the same table as products. **Toma de presión**, **inyectología**, **glucometría**, **domicilio** and **asesoría farmacéutica** are rows in `items` with `tracks_stock = false` (A7). They carry a price, an IVA class and a margin; they will carry a ticket line and a fiscal line; they move no stock. Nothing downstream branches on the distinction — the till, the documento equivalente, the margin report and every KPI treat a service as a product whose sale writes no `stock_moves` row. This costs one boolean and one `CHECK` constraint, and it buys the absence of a second product-like table with its own editor, its own picker, its own tax handling and its own bugs.

The catalog is also the network's, not a sede's. One tenant, one catalog, one set of laboratorios, categorías and proveedores, and prices that are network-wide by default with a per-sede override where a network genuinely prices differently (§2). And it is loadable: an internal management command takes an explicit tenant, pins it before touching anything (ledger rule 6), reads a documented set of CSVs and reports every row it could not place, rather than guessing. That command is how a pilot's first fifteen years of product data arrives, and it is a dry run unless told otherwise.

And none of it is a precondition for showing the product. A second command builds **Droguerías La 45** out of nothing — six sedes, seven laboratorios, Coopidrogas, 4.284 items with their barcodes, their registros and their precios, and a handful of servicios — so every screen this stage draws fills on a machine that has never seen a client's export (§1). That seed is synthetic, says so in the database, refuses to run against a tenant that already holds real rows, and is the fixture registry every later stage hangs its own data on.

What this stage does not do is decide anything about stock. There is no quantity anywhere in it. `items.tracks_stock`, `tracks_lots` and `tracks_expiry` are the three switches S3 reads to decide what a movement means, and S1 writes them and stops.

## Inherits

From **S0**, which is the only stage before this one:

- The four-service stack — `web`, `worker`, `postgres`, `caddy` — from one compose file, with `web` and `worker` on the same image (§9).
- Two Postgres roles: a migration role owning every table, a runtime role owning none, which is the precondition for every policy below being real (A1).
- **The table convention** every migration in this document follows and does not restate: `id` (uuid), `tenant_id`, `created_at`, `updated_at`, an RLS policy `USING (tenant_id = current_setting('app.tenant_id')::uuid)`, and `FORCE ROW LEVEL SECURITY` (§3, A1).
- **The tenant-pinning path, for all five contexts** (ledger rule 6). This stage uses two of them: the HTTP request, and **the management command, whose tenant is an explicit required argument and which pins before doing any work**. **This stage ships two commands on that path** — the load tool and the demo seed — and each pins before it reads a row. S1 never issues its own `SET LOCAL` and never invents a second pinning helper (cross-stage services).
- The four-role enum — `platform_admin`, `owner`, `admin`, `cashier` — and the single permission dependency every endpoint calls (§2).
- `tenants`, including the `settings` JSONB column. **S1 inherits the column and owns no key group in it** (ledger rule 5); see *Data* for why the catalog needs none.
- `locations` — read, never written (ledger rule 3). `item_prices.location_id` points at it, and a price scoped to a sede is a **sede** in the interface (§3 glossary).
- `users` — read for the audit trail and for `imports.started_by_user_id`.
- `audit_log` and **the audit write path**. Every elevated-role mutation this stage exposes appends through it; S1 does not re-expose the reader (§3, ledger).
- Procrastinate's queue and a running `worker`, so this stage adds one job and nothing else (§9).
- The django-ninja API with `/api/openapi.json`, the typed frontend client generated from it, and the endpoint convention: every application path carries the `/api/` prefix and is English (§3, §9).
- **The grid contract** as a primitive: `manualPagination`, `manualSorting`, `manualFiltering`, `rowCount` from the API, with page, size, sort and filter state in TanStack Router's typed search params so any view is a link (§9, design-system §B.4).
- The design-system Part B component layer — the eight-step type scale (§B.1), four surface levels (§B.2), table density modes and row states (§B.4), form controls and the one focus ring (§B.5), button sizes and variants (§B.6), the five status families and their tints (§B.7), the shell and the settings dialog (§B.8), skeleton, empty and error treatments (§B.10), and `j`/`k` on every office list (§B.13.2).
- The application shell with one empty, role-gated route per later-stage surface, for its owning stage to fill (§B.8).
- Django admin as the platform-admin surface, which is where this stage's load tool is run from or beside, never a tenant-facing screen (§2, §9).

## Scope · In / Out

### In

1. **`items` — one table for products and services** (A7, ledger). The full column set from §3 plus the four decisions this document takes: the base unit, the price grain, the service's cost of goods and the loader's external key. `tracks_stock = false` is the only thing that makes a service a service, and nothing else in the row is special-cased.
2. **`item_barcodes`** — several codes per item, which is the normal case and not an anomaly (ledger): the manufacturer's EAN, the distributor's, and one the droguería printed itself. Exactly one primary per item; a code resolves to exactly one item within a tenant.
3. **`manufacturers`** — the **laboratorio** (§3 glossary): Genfar, Tecnoquímicas, MK, La Santé, Procaps, Bayer, Baxter in the handoff data. Not a testing laboratory.
4. **`categories`** — two levels, no deeper. Flat enough to filter, nested enough to roll up (§3).
5. **`suppliers` and `supplier_items`** — Coopidrogas in the handoff data, plus whatever a network buys direct. Several suppliers per item is normal. S1 writes both; S6 later writes `suppliers.lead_time_days` from observed receiving and `supplier_items.cost` from what a receipt actually paid (ledger).
6. **`item_prices`** with `location_id` nullable (null = network-wide), effective dating, and `source` restricted to `manual` and `imported`. `regulated_max_price` is created empty here and populated only by S7 (ledger, disputed columns).
7. **`customers`** — created and loaded here. **S4 is the only interactive writer**; a customer is created at the counter (ledger).
8. **`imports`** — one row per run of a load tool, dry runs included. S6's sales-history loader records its runs in the same table (ledger).
9. **The four enums**: `item_type`, `vat_class`, `invima_status`, `price_source` (ledger).
10. **`invima_registration`, `invima_expires_at` and `invima_status` as first-class columns**, not custom fields (§3). Filterable in the grid, shown on the item detail, badged per design-system §B.7.4, and audited on every change so that "what was its state when we sold it" is answerable later. Botica validates nothing against INVIMA's register (§12).
11. **`vat_class` per item**, because the great majority of medicines are excluded from IVA under article 424 of the Estatuto Tributario while a large share of what a droguería sells — cosmetics, toiletries, drinks, devices — is not, so tax is computed per line and never per ticket (§3). S1 also fixes the class-to-rate mapping as a code constant, not a table and not a tenant setting.
12. **The internal load tool** as a management command taking an explicit tenant argument and pinning before any work (ledger rule 6). **Master data only** — manufacturers, categories, suppliers, items, barcodes, supplier links, prices, customers. Opening stock and lots are S3's; sales history is S6's.
13. **The demo seed** — the command the ledger's cross-stage services register names as S1's, the fixture registry every later stage adds its own data through, and this stage's own fixture: the tenant's shape, the catalog, the suppliers and the customers (§1, ledger).
14. **One job**: the nightly sweep that moves an item from `invima_status = valid` to `expired` when its registration date passes.
15. **The catalog grid** — `Inventario · Catálogo`, on S0's grid contract, with one search field matching across product name, laboratorio, barcode and INVIMA registration.
16. **The item editor** — the record panel that reads and writes one item, including its barcodes, its price, and its supplier links.
17. **Three settings sections** — `Laboratorios y categorías`, `Proveedores`, `Clientes` — under a new `Catálogo` group in the settings dialog's rail (design-system §B.8.4·4).
18. **The catalog combobox**, a searchable picker over thousands of rows, built once here and used by S3's receiving, S4's product lookup, S6's order lines and S7's price grid (design-system §B.5.4).
19. **Audit rows** on every mutation this stage exposes, through S0's path (ledger).

### Out

- **Stock in any form** — quantities, `lots`, `stock_moves`, `stock_on_hand`, `stock_policies`, the `Existencias` screen and the derived stock state — **S3** (§13, ledger). S1 writes the three switches S3 reads and no quantity.
- **`lots.invima_registration`**, the lot's own nullable copy of the registration it was released under — **S3**, which creates `lots` (§3, ledger).
- **The `Precios` screen, `item_prices.regulated_max_price`, every `source = model` price row, `elasticity_estimates` and `price_proposals`** — **S7** (ledger, design-system §B.8.4·1). S1 ships price *editing* inside the item editor; it ships no pricing module.
- **The sales-history loader** and every `sales` row with `source = imported` — **S6** (ledger, §11.2). Both loaders record their runs in `imports`; only S6's touches sales.
- **Purchase orders, goods receipts, and the write-back of `supplier_items.cost` and `suppliers.lead_time_days`** — **S6** (ledger).
- **`item_warnings` and `cross_sell_rules`** — **S8** (ledger). The safety layer is loaded alongside the catalog and is owned by the stage that reasons with it, not by the stage that holds the products.
- **Creating a customer at the counter, and the customer-identification step the documento equivalente requires** — **S4** and **S5** (ledger, §8).
- **Syncing any of this to a till**: the sync registry, the scoping predicate per table, the delta cursor index and the row-count estimate are **S2**'s, under ledger rule 9. S1 names the candidate set in *Hands off* and amends nothing.
- **Any target's own acquirer code vocabulary.** `customers.document_type` stores the document type in the readable domestic vocabulary a Colombian counter actually uses — `CC`, `CE`, `NIT`, `TI`, `PA`, `PEP`, `PPT` — and **each target's mapping in S5 translates it** into whatever codes the client's invoicing system expects (§8). That is exactly what a per-target mapping is for: a system that spells the same seven things differently costs seven lines in a mapping and no migration on this table, and a second client running a second system does not change it either. What *would* be a change here is a target that needs a value we do not capture **at all**, rather than one we name differently — see *Gated on*.
- **Every demo-seed fixture this stage cannot write.** Opening stock and lots are **S3**'s, shifts and counter sales **S4**'s, purchase orders and forecasts **S6**'s, price proposals **S7**'s, assistant traffic **S8**'s, the rollups the Panel reads **S9**'s, checklist entries **S10**'s. S1 owns the command and the registry; each stage owns the fixture that fills its own screens and is not finished until they render from it (§1, ledger).
- **A tenant-facing import wizard.** The load tool is internal and is run by us during onboarding, which is the deck's own rollout (§1). See *Gated on*.
- **A custom-fields UI.** `items.custom` is JSONB written by the loader and read as model features by S6 and S7 under their own keys (ledger, disputed columns); there is no screen for defining a field (§12).
- **Product images.** There are no images and no third-party logos anywhere in this system (design-system §A.12).
- **EPS and convenio price lists**, per-payer pricing, and loyalty pricing — not in v1 (§12). `item_prices.location_id` scopes a price list to a sede and nothing further, which is also one of A10's distributor extension points.
- **Any offline capability.** Every surface in this stage is an office surface (§4). See *Offline*.

## Data

Every table below carries the S0 convention — `id`, `tenant_id`, `created_at`, `updated_at`, the RLS policy and `FORCE ROW LEVEL SECURITY` — and it is not restated per row. Only stage-specific detail is listed.

| Table | Change | Detail |
|---|---|---|
| — | add enum | `item_type`: `product` \| `service` (A7, ledger) |
| — | add enum | `vat_class`: `excluded` \| `exempt` \| `rate_5` \| `rate_19` (§3, ledger) |
| — | add enum | `invima_status`: `valid` \| `in_process` \| `expired` \| `not_applicable` (§3, ledger) |
| — | add enum | `price_source`: `manual` \| `imported` \| `model`. **S1 writes the first two only**; every `model` row is S7's (ledger) |
| `items` | create | `type`, `name`, `description`, `manufacturer_id` nullable, `category_id` nullable, `presentation`, `active_ingredient`, `strength`, `invima_registration`, `invima_expires_at`, `invima_status`, `requires_prescription`, `controlled`, `cold_chain`, `unit`, `splittable`, `units_per_pack`, `vat_class`, `tracks_stock`, `tracks_lots`, `tracks_expiry`, `active`, `custom` JSONB (§3) |
| `items` | create — coined | `external_code` nullable, `UNIQUE (tenant_id, external_code)` where present: the previous system's own product code. It is what makes the loader idempotent and re-runnable, and it is the only stable handle a legacy export has — a barcode is not one, because an item legitimately carries several and a droguería prints its own (§11.2) |
| `items` | create — coined | `service_cost` nullable: the standing cost of delivering one unit of a service, which is what §3's "a service is a product with no cost of goods **unless one is entered**" requires a home for. S4 stamps `sale_lines.unit_cost` from it exactly as it stamps from the lot for a product. Null means no cost of goods and a 100% margin |
| `items` | constraints | `CHECK (tracks_stock OR NOT (tracks_lots OR tracks_expiry))` — a service cannot track lots or expiry. `CHECK (units_per_pack >= 1)`. `CHECK (NOT splittable OR units_per_pack > 1)`. `CHECK (type = 'service' OR service_cost IS NULL)`. `UNIQUE (tenant_id, name, presentation)` — two rows for the same presentation of the same product is the defect a catalog cleanup exists to remove |
| `item_barcodes` | create | `item_id`, `code`, `is_primary`. `UNIQUE (tenant_id, code)` and a partial `UNIQUE (tenant_id, item_id) WHERE is_primary` |
| `manufacturers` | create | `name`, `nit` nullable. `UNIQUE (tenant_id, name)`. The **laboratorio** (§3) |
| `categories` | create | `name`, `parent_id` nullable self-reference. `UNIQUE (tenant_id, name, parent_id)`. `CHECK` that a parent's own `parent_id` is null — two levels, enforced, never three |
| `suppliers` | create | `nit`, `name`, `contact`, `payment_terms`, `lead_time_days` (§3). `UNIQUE (tenant_id, nit)` where present. `lead_time_days` lands from the loader or by hand and is overwritten by S6 from observed receiving (ledger) |
| `supplier_items` | create | `supplier_id`, `item_id`, `supplier_code`, `cost`, `min_order_pack` (§3). `UNIQUE (tenant_id, supplier_id, item_id)`. `cost` is per **purchase pack**, not per base unit — see the base-unit rule below |
| `supplier_items` | create — coined | `is_preferred` boolean, default false, at most one true per item: which supplier S6 orders from when an item has several. Created here under ledger rule 1 so S6 never migrates a table it does not own |
| `item_prices` | create | `item_id`, `location_id` nullable, `price` `numeric(12,2)`, `effective_from` date, `effective_to` date nullable, `regulated_max_price` nullable (**S7 writes**), `source` (§3, ledger). Partial `UNIQUE (tenant_id, item_id, location_id) WHERE effective_to IS NULL` — one open row per item per scope, always |
| `customers` | create | `document_type`, `document`, `name`, `phone`, `email`, `address`, `data_consent`, `notes` (§3). `UNIQUE (tenant_id, document_type, document)` where `document` is present. `document_type` is text over `CC`, `CE`, `NIT`, `TI`, `PA`, `PEP`, `PPT` |
| `customers` | create — coined | `data_consent_at` timestamp nullable: a boolean alone cannot answer when consent was given, and Ley 1581 asks (§7) |
| `imports` | create — coined columns | §3 names the table and no columns. Coined: `kind` (text: `catalog` and `demo_seed` here, `sales_history` at S6), `source` (the file or label the run read), `status` (text: `running` \| `completed` \| `failed`), `dry_run`, `started_at`, `finished_at`, `rows_read`, `rows_created`, `rows_updated`, `rows_failed`, `errors` JSONB, `started_by_user_id` nullable — a management command has no HTTP user |
| indexes | create | On `items`: `(tenant_id, active, name)` for the default grid page, `(tenant_id, manufacturer_id)`, `(tenant_id, category_id)`, `(tenant_id, invima_status)`, `(tenant_id, invima_expires_at)` for the sweep, plus a trigram index on `name` and on `invima_registration` for the search field. On `item_barcodes`: the unique code index already serves the barcode lookup. On `item_prices`: `(tenant_id, item_id, location_id, effective_from)` for resolution |

**S1 migrates no index onto a table it does not own** (ledger rule 4), and it needs none: every read path in this stage is over its own tables.

**S1 owns no `tenants.settings` key group** (ledger rule 5). This is deliberate rather than an omission. The two candidates for one — a default `vat_class` for a new item, and a default `unit` — are exactly the two values that must never be defaulted (below), so a group would exist only to hold settings that should not exist.

### The base unit, `splittable` and `units_per_pack`

**`items.unit` is the base unit, and every quantity anywhere downstream in this product is in base units** — `stock_moves.quantity`, `stock_on_hand.quantity`, `sale_lines.quantity`, `purchase_order_lines.*`, every forecast and every report. §3 already says the ledger's quantities are signed base units; this is the stage that decides what one is.

- For an item with `splittable = false`, the base unit is the pack the customer walks out with. A box of Acetaminofén 500 mg × 100 is **one** unit, `unit = 'caja'`, `units_per_pack = 1`.
- For an item with `splittable = true`, the base unit is the smallest unit the counter will sell — one tableta, one sobre, one blíster. `units_per_pack` is how many base units are in the pack the item is **bought and received as**: 30 for a box of 30 tabletas. It exists so a goods receipt of 4 boxes becomes 120 base units, and so a supplier cost quoted per box divides down to a base-unit cost.
- **`item_prices.price` is always per base unit.** A box price is `price × units_per_pack`, shown as a derived figure and never stored as a second row. There is exactly one price per item per scope per moment.

**What breaks if this is wrong.** The model cannot express a box priced at a discount to thirty times the unit price, which is a commercially ordinary thing for a droguería to want. If the pilot's catalog contains a single item whose box price is not `units_per_pack ×` its unit price, this decision is wrong *before the first sale* and the fix is a `pack_size` column on `item_prices`, a second resolution rule, and a case in S4's line arithmetic — cheap now, expensive after a till is selling. **The measurement that decides it** is one query against the client's price export, and it belongs in the same conversation as §11.2.

**Rounding.** `price` is stored to the centavo because a box of 30 at $12.000 divides to $400,00 and a box of 30 at $12.500 does not. Every *displayed* and *charged* figure is a whole peso (design-system §A.11), and **the rounding happens once, at the line total, half-up** — three tabletas at $416,67 charge $1.250, not `3 × $417 = $1.251`. If rounding moves to the unit price instead, a box sold tablet by tablet returns a different amount than the box, and the drift is invisible until someone reads a margin report.

### `vat_class`, and what a rate is

The four values are not three rates plus a spare. `excluded` is not a taxable operation at all; `exempt` is taxable at 0% and carries a right to credit; `rate_5` and `rate_19` are the general rates. The distinction between the first two matters to an accountant and to the documento equivalente, which is why §3 enumerates four.

**The class-to-rate mapping is a code constant owned by this stage — not a table, not a `tenants.settings` key, not a per-item number.** The rates are statute; a tenant does not get to set them, and a table invites a network to. **What breaks if this is wrong**: a rate changed by decree becomes a code change and a dated constant rather than a data edit. That is survivable precisely because `sale_lines` stamps `vat_class` and `tax_amount` at the moment of sale (§3) rather than recomputing from the item, so no historical ticket or fiscal document moves when the constant does.

**A new item has no default `vat_class`.** The field is required in the editor with nothing preselected, and the loader fails a row that does not carry one. Defaulting to `excluded` — the statistically right answer for a medicine — silently under-charges IVA on every cosmetic, drink and device loaded without the column, and that is a DIAN problem measured in sanctions (§8), not a data-quality problem. The cost of the decision is one click per item.

### The registro INVIMA, and what the four states mean

`invima_registration` is the sanitary registration a product is legally sold under. It is **a first-class column, not a custom field** (§3): it expires on its own schedule, independent of any lot's expiry date, and a droguería is inspected against it. It gets its own `invima_expires_at` and its own `invima_status`, and it is one of the four things the catalog's search field matches.

`invima_status` is **stored, not derived**, for the plain reason that two of its four values are not derivable from a date. The nightly sweep keeps the derivable transition honest and touches nothing else.

| Value | Means | Where the date is |
|---|---|---|
| `valid` | The registration is current | `invima_expires_at` is set and in the future |
| `in_process` | Renewal is filed and INVIMA has it. The product is normally still sellable while the file is open, and that is the pharmacy's call, not Botica's | May be set and past; the sweep does not touch this value |
| `expired` | The registration lapsed, or an administrator marked it so by hand | Set and past, or unknown |
| `not_applicable` | The row is not a registrable product — every service, and the non-medicinal lines a droguería sells | Null |

**Botica records the state and the pharmacy's decision; it validates nothing against INVIMA's register** (§3, §12). So `expired` is a badge (§B.7.4) and a grid filter and **never a disabled row, never a blocked sale, and never a modal a cashier has to dismiss**. What S1 owes S4 for the counter flag is exactly two fields on the item the till already syncs — `invima_status` and `invima_expires_at` — and the till's treatment of them is S4's to draw.

**What records the pharmacy's decision** is the sale itself, joined to the audit trail: `sale_lines` names the item, and `audit_log` carries every change to the three INVIMA columns with its actor and its timestamp, so "what was its registration state on the day we sold it" is answerable from data this stage already writes. **No acknowledgement column is coined here.** If the pilot needs an explicit "sold anyway, acknowledged by" record, it is a column on `sale_lines` and it belongs to S4, which owns that table — not a column on `items`, which has no idea a sale happened.

### Price resolution

**The price in force for an item, at a sede, at an instant is: the row whose window contains the instant, preferring a `location_id` match over the network-wide row; within one scope, the latest `effective_from` wins.** A price is never edited and never deleted once it has been in force — a new row is created and the previous row's `effective_to` is closed in the same transaction. A future-dated row simply becomes in force on its date; **there is no activation job**, because resolution happens at read time.

The consequence, and it is the one the architecture already anticipated: a till that was offline when a price changed sells at the price it had. That is not corrected — `sale_lines.unit_price` is the record of what was actually charged, and the difference is reported rather than repaired (§5).

### The Ley 1581 deletion, and why the acquirer is re-rendered rather than snapshotted

`customers` is master data that later stages point at: `sales.customer_id` names the acquirer, and S5 builds the acquirer block of the canonical sale document by reading the customer **through the sale, at the moment it hands the sale over** — it stores no acquirer snapshot, deliberately. Two decisions follow, and both are this stage's because it owns the table and the endpoint.

**A customer any sale references is never hard-deleted.** `DELETE /api/customers/{id}` removes the row outright only while nothing points at it — the ordinary case of a row typed wrong at the counter two minutes ago. Once a `sales` row names the customer, the same call **erases the identifying fields in place**: `document_type`, `document`, `name`, `phone`, `email`, `address` and `notes` are cleared, `data_consent` and `data_consent_at` with them, while the row and every `sales.customer_id` referencing it survive untouched. The response states which of the two happened and names the sale count, because an administrator who pressed one button is owed the difference. **No column is coined for it**: this mutation already writes an `audit_log` row carrying the actor, the entity and both before and after, which is what answers "who erased this and when" — the same argument that keeps an acknowledgement column off `items` above. In `Clientes` such a row's `Cliente` cell reads `Cliente eliminado` in `#909090` (§B.9.2, tier 3), derived from the absent name and document rather than from a flag.

**What breaks if this is wrong.** Hard-deleting instead would either orphan the acquirer on a closed sale or cascade into `sales`, and a sale that loses its customer is a ticket whose counterpart in the client's invoicing system names a person Botica can no longer produce. Refusing outright — the rule an earlier draft handed to S5 — leaves a network with no way at all to honour an erasure request, which is the obligation this endpoint exists to meet.

**Why re-rendering is the right default.** An acquirer snapshot taken when the sale closed would freeze whatever was wrong at that instant — a NIT missing its check digit, an address entered from the wrong street. Re-rendering from the current row means **an address corrected today is correct on a document sent tomorrow**, and a handoff stuck at S5 is repaired by fixing the customer and retrying rather than by editing a document. The cost is the honest one and it is small: after an erasure, a retry of an old sale renders an incomplete acquirer block, which S5 validates before sending and lands on its work list in words — a loud failure, and strictly better than silently handing over an acquirer the customer never gave. What was actually handed over is not lost either: it is on S5's `fiscal_documents.payload`, which is evidence of a past attempt and is never read back as input.

### The load tool

A management command, not an endpoint and not a screen. It takes an **explicit tenant argument and pins it before doing any work** (ledger rule 6), because a loader that inferred a tenant from a file would be a loader that could write a network's catalog into another network's, and under `FORCE ROW LEVEL SECURITY` an unpinned run would silently write nothing at all, which is the worse failure of the two.

**Input is one CSV per entity, with a fixed header, applied in a fixed order** — `manufacturers` → `categories` → `suppliers` → `items` → `item_barcodes` → `supplier_items` → `item_prices` → `customers`. The order is the reference order: an item names a laboratorio, a barcode names an item, a supplier link names both. A run may supply a subset; a file whose references are not yet present fails its rows rather than creating a placeholder, because a placeholder laboratorio called `GENFAR ` with a trailing space is precisely the debris a catalog cleanup exists to remove.

**A run is a dry run unless `--apply` is passed.** The cost of that decision is one flag; the cost of the opposite is a pilot's catalog silently doubled by a re-run somebody thought was a preview. A dry run reads, validates, resolves every reference and reports exactly what it would create and update — and it writes its `imports` row like any other run, with `dry_run = true`, so a preview is on the record too.

**Idempotency is per entity, on a natural key**, so the same file applied twice is a no-op: `items` on `external_code`, falling back to the primary barcode when the export has no code; `manufacturers` on `name`; `categories` on name plus parent; `suppliers` on `nit`; `supplier_items` on the supplier-and-item pair; `customers` on `document_type` plus `document`; `item_prices` on item, scope and `effective_from`. A row that matches an existing record **updates it**; a row that matches nothing creates one.

**What the loader will not do**, each because the alternative is a silent data defect rather than a loud run failure:

- It never guesses a `vat_class`. A row without one fails. An operator may pass `--vat-class-when-missing=<value>`, and the run records in its `imports` row how many rows took it — turning a guess into a recorded operator decision.
- It never deletes and never deactivates. A product missing from a new export is a product the export forgot, not a product the network stopped selling, and deciding otherwise from an absence is how a catalog loses its slow movers.
- It never creates a reference it was not given, per the paragraph above.
- It never writes stock, a lot, a sale or a `model` price row. Those belong to S3, S6 and S7 (ledger).

**Reporting.** Every run writes one `imports` row carrying `rows_read`, `rows_created`, `rows_updated`, `rows_failed` and an `errors` JSONB holding, per failed row, its file, its line number, the value that failed and the reason. The command prints the same summary and **exits non-zero if any row failed**, so an operator running it over eight files knows without reading eight screens. `GET /api/imports` reads the same rows back, which is how an administrator sees what onboarding did to their catalog without asking us.

### The demo seed

**One command builds a tenant that looks like the handoff**, and the ledger names S1 its owner (cross-stage services, §1). `seed_demo_tenant` takes an **explicit tenant argument and pins before any work** (ledger rule 6) — the same discipline as the loader, for the same reason — and it **does not create the tenant**. S0's Django admin creates one today and S10's `provision_tenant` creates one later; a seed that created its own would be a third provisioning path, and it would drift from the other two the first time a manifest changed. It writes `locations` and the three demo users **through S0's own creation paths rather than by hand**, so the seed sequences writers instead of becoming a second one — the discipline S10's provisioning already applies to this stage's loader.

**Why this is a deliverable and not a fixtures file.** A platform that needs a data migration before it can be shown is a platform that cannot be sold (§1). The seed is what makes the demo, the pilot's first morning and every developer's local database the same shape, and **a stage is not finished until its screens render convincingly from it** — a sharper completion test than a green suite, because it catches the empty state nobody designed and the tile whose denominator is zero.

**What S1's own fixture builds.**

| | |
|---|---|
| Tenant | **Droguerías La 45**, the name the handoff draws in the sidebar, on slug `demo-la-45` |
| Sedes | six `locations` of type `store` — **Chapinero, Kennedy, Suba, Restrepo, Bosa, Usme** — which is the `Sede · Todas · 6` chip the Existencias filter bar draws |
| Users | one `owner`, one `admin` (`Marcela Ríos`) and one `cashier` homed at Chapinero (`Andrés Peña`) — the shell's user footer draws the second and the third by name, the read-only-`cashier` acceptance needs the third to exist, and the `owner`-only surfaces, the Ley 1581 deletion among them, need the first |
| Laboratorios | the handoff's seven: **Genfar, Tecnoquímicas, MK, La Santé, Procaps, Bayer, Baxter** |
| Categorías | two levels — `Medicamentos` over analgésicos, antibióticos, cardiovascular, digestivo, respiratorio, antialérgicos and metabólico; `Cuidado personal`; `Bebidas y sueros`; `Dispositivos médicos`; `Servicios` — enough for the `Categoría` chip to be worth opening and for S8's `symptom_category_map` to have something to bind to |
| Proveedores | **Coopidrogas**, the distributor the Compras filter chip names, plus three direct accounts. `supplier_items` covers the catalog with exactly one `is_preferred` per item and a `cost` below the price everywhere, so no margin figure on any screen is negative |
| Items | **4.284**, of which five are services — which is what makes the drawn grid footer read `1-15 de 4.284` and the pagination end at page **172** at 25 rows a page |
| Códigos de barras | one primary EAN-13 per item with a valid check digit, drawn from the in-store `200`–`299` prefix range so no seeded code can collide with a real manufacturer's GTIN; a couple of hundred items carry a second and a third, so "several codes per item is normal" is visible rather than asserted |
| Registro INVIMA | plausible registrations spread across all four `invima_status` values, a dozen expiring inside 90 days and a handful already past — so the badge, the `Vence` filter and the nightly sweep each have rows to act on the moment the tenant exists |
| Precios | one open network-wide `item_prices` row per item, a handful scoped to Chapinero, and one dated a week ahead |
| Clientes | around forty, over all seven `document_type` codes, some with `data_consent` stamped and some without, and at least one no sale will ever reference — so the Ley 1581 deletion has both of its cases the moment S4's fixture lands |

**The named rows are literal and the bulk is generated.** The fifteen products the Existencias screen lists by name exist with the laboratorio drawn beside each — Acetaminofén 500 mg × 100 · Genfar, Sales de rehidratación oral · Tecnoquímicas, Losartán 50 mg × 30 · MK, Amoxicilina 500 mg × 20 · La Santé, Omeprazol 20 mg × 30 · Procaps, Ibuprofeno 400 mg × 50 · Genfar, Metformina 850 mg × 30 · MK, Loratadina 10 mg × 10 · Tecnoquímicas, Enalapril 20 mg × 30 · La Santé, Atorvastatina 20 mg × 30 · Procaps, Suero fisiológico 500 ml · Baxter, Naproxeno 500 mg × 20 · Genfar, Dipirona 500 mg × 10 · Tecnoquímicas, Salbutamol inhalador 100 mcg · Bayer, Hidroclorotiazida 25 mg × 30 · MK — as do the eleven the Compras screen orders and the four the Mostrador ticket and its suggestion cards name. **S1's fixture carries the half of each drawn row that lives in `items`, `manufacturers` and `item_prices`; the lote, the vencimiento, the sede and the cantidad are S3's fixture, registered against this one.** Everything else is generated from a name-strength-pack grammar over the same seven laboratorios and the same category tree, from **one fixed random seed**, so the fixture is identical on every machine. *What breaks if this is wrong:* a fully generated catalog produces a demo whose every row disagrees with the screenshot the client was shown, and the first person to compare the two stops trusting both.

**Ids are derived, not random.** Every seeded row takes a uuid v5 over a fixed namespace and the fixture's own natural key, so a rebuilt seed keeps the id it had. A demo script, a screenshot, a saved link and a bug report therefore all still point at the same row after somebody resets their database, which is not true of a fixture that lets the database assign ids.

**Seeded prices carry `source = 'imported'`, never `manual`.** They arrived from a file and no person typed them, and the enum admits nothing else — `model` is S7's (ledger). The useful consequence is that a price changed on screen during a demo writes a `manual` row and closes the `imported` one, so the price-history acceptance is demonstrated live rather than staged. The drawn figures are exact where a screen shows one: `$3.900` for the sales de rehidratación oral, `$8.400` for the loperamida, `$5.200` for los electrolitos and `$2.600` for el acetaminofén de 10, so the Mostrador ticket totals `$15.600` (§A.11).

**Five services, so the mixed catalog is visible on the first screen anyone opens** (A7): toma de presión, inyectología, glucometría, domicilio and asesoría farmacéutica, each with `tracks_stock = false`, a price and a `vat_class`. One carries a printed barcode, so the scan path over a service is demonstrable; two carry a `service_cost` and three leave it null, so both the costed service and the 100%-margin one have a row.

**The registration mechanism.** One registry, `DemoFixture`, and every later stage adds itself to it rather than to the command. A fixture declares four things: its `name`, the fixture names it `requires`, the `guard_tables` that must be empty for a seed to be allowed to run at all, and a `build(tenant)` that writes its own rows through its own stage's writers. Discovery is Django's autodiscovery over installed apps — one `demo_fixtures.py` per app — ordering is a topological sort over `requires` with `catalog` at the root, and **the whole run is a single transaction**: a fixture that raises takes the run with it, because a half-seeded tenant is worse than an unseeded one and would be reported as a product bug rather than as a failed command. `--only <fixture>` rebuilds one stage's data while that stage is being written, `--list` prints the registry in the order it would run, and the command prints rows created per fixture and **exits non-zero on any failure**, which is the loader's summary discipline and not a second one.

**Invariant 1 — the data is self-evidently synthetic.** What is on screen reads as real, because a demo whose sidebar says `TENANT DEMO 1` demonstrates nothing; what is in the database does not. Every NIT and every customer document comes from a reserved range no Colombian registry issues, every email is at `example.com` (RFC 2606), every phone is `+57 601 000 0000`, the slug is `demo-la-45`, and each run writes one `imports` row with `kind = 'demo_seed'` naming every table it touched — which is both the record of the run and the marker on the tenant. `imports.kind` being text rather than a Postgres enum is what lets that exist without a migration (*Data*). **The command is never exposed over HTTP**: no endpoint, no button, no settings toggle, and nothing in the tenant-facing product invokes it.

**Invariant 2 — it refuses a tenant that already holds real rows.** Inside the pinned transaction and before any write, the command counts the `guard_tables` every registered fixture declared — at S1 they are `items`, `customers` and `imports` — and refuses, naming the table and the count, unless the tenant is empty or its only `imports` rows are of kind `demo_seed`. `--reset` deletes the seeded tenant's rows and rebuilds them; it is the one path in this product that hard-deletes an item, it is reachable from no surface, and **it is subject to the same guard rather than a bypass of it**. *What breaks if this is wrong:* a seed run against a live tenant merges four thousand fictional products into a real catalog with no undo — they are indistinguishable from a bad import to everyone who does not read `imports`, and a pharmacist would be selling against them by that afternoon.

### Search, and the budget it has to meet

One field, one query param, four things matched: product `name`, the laboratorio's `name`, any `item_barcodes.code`, and `invima_registration`. A barcode and a registration number are matched **exactly**; a name and a laboratorio are matched as a prefix and as a trigram, so `losar` finds `Losartán 50 mg × 30` and `acetaminofen` finds `Acetaminofén`.

The binding number is §4's: **the inventory grid page, on any filter combination, in under 400ms p95 server time**, server-paginated. At the handoff's 4.284 rows that is comfortable across a three-table join, and the indexes in the table above are what make it so. **If the pilot's catalog and that budget disagree**, the answer is a maintained search column on `items` — a migration on a table this stage owns, changing no endpoint contract and no screen. It is named here so nobody reaches for a search service, which would be a fifth container against an architecture whose defining choice is that Postgres does everything (§4).

### What is meaningless for a service, and what the editor does about it

An item with `tracks_stock = false` leaves eleven columns without meaning. The editor does not render them, and the loader rejects a service row that fills them, so the meaninglessness never becomes stale data somebody later trusts.

| Column | For a service | Editor |
|---|---|---|
| `manufacturer_id` | null | Laboratorio field not rendered |
| `presentation`, `active_ingredient`, `strength` | null | Presentación section not rendered |
| `invima_registration`, `invima_expires_at` | null | Registro sanitario section not rendered |
| `invima_status` | forced `not_applicable` | Read-only, rendered as the badge **No aplica** (design-system §B.7.4) |
| `requires_prescription`, `controlled`, `cold_chain` | false | Manejo section not rendered |
| `splittable` | false | Not rendered |
| `units_per_pack` | 1 | Not rendered |
| `tracks_lots`, `tracks_expiry` | false, by `CHECK` | Not rendered |
| `supplier_items` | none | Proveedores section not rendered — a service is not purchased |

What stays, and is the whole of a service: `type`, `name`, `description`, `category_id`, `unit` (`servicio`, `sesión`, `domicilio`), `vat_class`, `active`, `custom`, `service_cost`, `external_code`, its `item_prices` rows, and — deliberately — its `item_barcodes`. A droguería that prints a code for inyectología so the counter can scan it gets the same scan path as a box of pills, because the till's lookup does not know the difference and should not have to (A7).

## API surface

Every path carries the `/api/` prefix and is English (§3). Every endpoint runs behind S0's single permission dependency (§2), inside the pinned transaction (A1), and every mutation appends to `audit_log` through S0's path (ledger). Sede scoping does not apply to any of these — the catalog is the network's (§2, A2) — and the one place a location appears is a price's own scope.

| Method | Path | Purpose | Who can call it |
|---|---|---|---|
| GET | `/api/items` | The catalog grid, server-paginated and server-sorted. Query params: `q` (matching name, laboratorio, barcode and INVIMA registration), `type`, `manufacturer_id`, `category_id`, `invima_status`, `active`, `barcode` (exact), plus page, size and sort (§9) | `owner`, `admin`, `cashier` read-only |
| POST | `/api/items` | Create a product or a service. `vat_class` is required and has no default | `owner`, `admin` |
| GET | `/api/items/{id}` | One item with its barcodes, its supplier links, and the price in force per scope | `owner`, `admin`, `cashier` read-only — **without `supplier_items.cost` or `service_cost`** |
| PATCH | `/api/items/{id}` | Edit the item, including its barcode set as an array. There is no separate barcode endpoint: a barcode has no life outside its item | `owner`, `admin` |
| — | — | **There is no `DELETE /api/items/{id}`.** Every later table in the product references `items`, and a hard-deleted item is a hole in a ticket, a stock ledger and a fiscal record. `active = false` is the only removal, and §2's hard-delete right applies to users, not to catalog rows | — |
| GET | `/api/items/{id}/prices` | Every price row for the item, per scope, with its window and `source` | `owner`, `admin` |
| POST | `/api/items/{id}/prices` | Create a price row, closing the open row in the same scope in the same transaction. `source` is `manual` from this endpoint, always | `owner`, `admin` |
| DELETE | `/api/item-prices/{id}` | Remove a future-dated row that was never in force. A row that has been in force is not deletable — it is what a past sale was made at | `owner`, `admin` |
| GET / POST | `/api/manufacturers` | The laboratorios | `owner`, `admin`; `cashier` read-only for the grid filter |
| PATCH / DELETE | `/api/manufacturers/{id}` | Delete refuses, naming the count, when any item references it | `owner`, `admin`; delete `owner` |
| GET / POST | `/api/categories` | Two levels, enforced. `parent_id` null is a top-level category | `owner`, `admin`; `cashier` read-only |
| PATCH / DELETE | `/api/categories/{id}` | Delete refuses when any item or child category references it | `owner`, `admin`; delete `owner` |
| GET / POST | `/api/suppliers` | The proveedores | `owner`, `admin` |
| PATCH / DELETE | `/api/suppliers/{id}` | Delete refuses when any `supplier_items` row references it | `owner`, `admin`; delete `owner` |
| GET | `/api/supplier-items` | The supplier↔item links, filterable by `supplier_id` and by `item_id`. Read by item from the item editor, by supplier from the settings section, and by item again by S6 | `owner`, `admin` |
| POST / PATCH / DELETE | `/api/supplier-items/{id}` | Written from the item editor. Setting `is_preferred` clears it on the item's other links in the same transaction | `owner`, `admin` |
| GET / POST | `/api/customers` | The clientes. Server-paginated; `q` matches document and name | `owner`, `admin` |
| GET / PATCH | `/api/customers/{id}` | Edit identity and consent. Setting `data_consent` stamps `data_consent_at` server-side | `owner`, `admin` |
| DELETE | `/api/customers/{id}` | A Ley 1581 deletion, and **the rule is decided here** rather than deferred to a later stage: a customer no sale references is removed outright; a customer any `sales` row references is **never hard-deleted** — its identifying fields are erased in place, the row and every `sales.customer_id` pointing at it survive, and the response names which of the two happened and the sale count. See *The Ley 1581 deletion* | `owner` |
| GET | `/api/imports` | The load-run log: kind, source, status, counts, and the per-row error list. Read-only; runs are created by the command, never by this endpoint | `owner`, `admin` |

**No settings pair.** S1 adds no `GET`/`PATCH /api/settings/{group}` (ledger rule 5) because it owns no key group.

## Jobs

One job, on S0's Procrastinate queue.

| | |
|---|---|
| **Job** | `expire_invima_registrations` |
| **Trigger** | Daily cron, once per tenant. `tenant_id` is part of the payload; the job opens its transaction and pins before touching anything (ledger rule 6) |
| **What it does** | Sets `invima_status = expired` on every item whose `invima_status` is `valid` and whose `invima_expires_at` is in the past. **It touches nothing else** — `in_process` stays `in_process` because INVIMA has the file, `not_applicable` stays, and a status an administrator set by hand to `expired` is never flipped back |
| **Idempotency key** | `(tenant_id, run_date)`. The work itself is a set-based update on a date comparison rather than a delta, so a re-run changes nothing and a **missed day is repaired by the next run** rather than lost |
| **Failure behaviour** | Retries with backoff on the queue. A run that never happens leaves an item reading **Registro vigente** past its date, which the grid's `Vence` filter still catches because that filter reads `invima_expires_at`, not the status. The status column is therefore a convenience for the badge and the enum filter, and the date is the truth |
| **Audit** | One `audit_log` row per run naming the count, with no actor, rather than one row per item — a thousand rows for a date passing is noise that buries the edits a person actually made. This requires `audit_log.actor_user_id` to admit null for a system actor, which every stage with a job needs and S0 owns |

There is no price-activation job and no barcode-reindex job. A future-dated price becomes current by the resolution rule at read time, and the search indexes are maintained by the database.

## UI

Design-system sections are binding. The interaction bar applies to every surface below without restatement: one focus-ring definition (§B.5.1), geometry-matched skeletons and no spinner outside a pressed button (§B.10.1), empty states that carry an action (§B.10.2), errors that name the operation and the recovery (§B.10.3), `j`/`k`/`Enter`/`x`/`Esc`/`/` on every list (§B.13.2), and every figure through the §A.11 formatter.

### Where the catalog lives

**The catalog is `Inventario · Catálogo`, a sibling route of `Inventario · Existencias`.** The nav is one flat list of seven items and it is at its ceiling (§B.8.1), so an eighth item is not available; and a four-thousand-row grid inside a 1120 × 720 settings dialog is the wrong container (§B.8.4·4). The two routes are switched by the drawn segmented control (§A.15.3) placed at the left of the page header's action slot: `Existencias` · `Catálogo`. The breadcrumb reads `Inventario`, the `t-28` title is the active segment's word, and the URL is `/inventory/catalog` — one title per route, honoured (§B.8.5).

**At S1 the module has one route and Catálogo is its landing.** S3 adds `Existencias`, takes the landing, and the segmented control gains its second segment. **If design rejects the segmented control here**, the fallback is an eighth nav item, which §B.8.1 explicitly resists, and this paragraph is the diff to apply.

### Catálogo — the grid

Standard density, 48px rows, on the grid contract (§B.4, §9). Columns as percentages under `table-layout: fixed` (§A.17): `Producto 26 · Laboratorio 13 · Categoría 12 · Presentación 11 · Registro INVIMA 12 · Precio 11 (right) · Registro 15`.

- **One badge column, and it is `Registro`** (§B.7.3), carrying `invima_status` as **Registro vigente** (positive, solid) · **En trámite** (warning, hollow) · **Registro vencido** (critical, solid) · **No aplica** (neutral, hollow), exactly per §B.7.4. `expired` is a badge and a filter and **never a disabled row** — Botica records the state and the pharmacy's decision, and blocks nothing (§3, §12).
- **`Tipo` is a filter chip, not a column.** A network has perhaps eight services against four thousand products, and a column reading `Producto` on 99,8% of rows spends 8% of the table saying nothing. The item detail states the type; the filter finds the services.
- A cell that does not apply to a row — a service's `Laboratorio`, its `Presentación` — renders an em dash in `#909090` (§B.9.2, tier 3 geometry), and this is stated once rather than per column.
- An inactive item, when the `Estado` filter admits one, renders its name followed by ` · Inactivo` at `t-12` `#727272`. It is not a second badge (§B.7.3).
- **Filter bar** (§A.13.3): the search field, then chips `Tipo`, `Laboratorio`, `Categoría`, `Registro INVIMA`, `Estado`. The search field takes a wider `min-width` than §A.15.2's 250px on this surface to hold its placeholder, **`Buscar producto, laboratorio, código o registro`**, which names all four things it matches. The filter bar's right slot is this surface's provenance line (§B.8.5): **`4.284 referencias activas · 12 servicios`**. It is **not** a sync line — nothing here reads the local store (§B.9.1).
- **Footer** (§A.17, §B.4.5): `1-25 de 4.284` on the left, and where the count is non-zero the annotation `18 con registro vencido` at 11px `#6b6b6b`, mirroring the drawn `312 requieren acción`. Right: the row-size select and the page group.
- **Loading**: twenty-five skeleton rows at the real 48px with a bar per cell at that column's real width (§B.10.1). A re-fetch after a filter, sort or page change dims the existing rows to `opacity:0.6` with the 2px progress line and does not blank them.
- **Empty — never populated**: `Todavía no hay catálogo` / `El catálogo de la red se carga desde el sistema anterior durante la puesta en marcha. También puede crear una referencia a mano.` / primary `Nuevo producto`. **This departs from §B.8.4·7's `Cargar catálogo`** on purpose: there is no tenant-facing import wizard in v1 and a primary button that opens nothing is worse than one that creates a product. See *Gated on*.
- **Empty — filtered to nothing**: `Ningún producto coincide con estos filtros`, echoing the active filters back verbatim, with a **secondary** `Quitar filtros` — the intent was to filter (§B.10.2).
- **Error**: route scope — the empty-state geometry, a `Reintentar`, and a selectable correlation id (§B.10.3).
- **Denied**: a `cashier` reaches the grid read-only. `Nuevo producto` is **not rendered**, never rendered disabled (§B.8.3). A `platform_admin` sees what an `owner` sees on a pinned tenant.

### The item editor — the record panel

440px, L2, pushing rather than overlaying, header at 64px with a `t-20` title and a ghost close, body scrolling at `padding:20px`, footer under a hairline carrying `[Cancelar: secondary] [Guardar: primary]` (§B.8.5, §B.6.2). `Nuevo producto` opens the same panel empty. Sections, separated by a hairline with 28px above it (§B.3): **Identidad** · **Presentación** · **Registro sanitario** · **Manejo** · **Impuesto** · **Precio** · **Códigos de barras** · **Proveedores**, minus everything the service table above removes, plus **Costo del servicio** for a service.

- Laboratorio, categoría and proveedor are searchable comboboxes, because a droguería's catalog is thousands of rows and a select is not a search (§B.5.4).
- Validation fires on blur and on submit, never on keystroke; the error replaces the help text in the same slot so validating shifts no layout (§B.5.7). `vat_class`, `name`, `type` and `unit` carry `Obligatorio` in the help slot.
- **Códigos de barras** is a list with an add row and one `Principal` radio across the set. A code already held by another item is refused **naming that item**, not with a uniqueness message — a cashier's scan must resolve to one item in under 50ms (§4) and an ambiguous scan sells the wrong product at the wrong price.
- **Precio** shows the price in force network-wide, any per-sede overrides as a short list, and the change history. Editing creates a new row and closes the old one; the panel says so in an 11px line rather than pretending the field was overwritten. A `regulated_max_price`, when S7 has written one, renders as a hollow warning dot after the figure with the cap stated below it (§B.8.4·1); at S1 that cap is always absent and the dot never appears.
- **`tracks_stock` is freely editable at S1** because nothing has moved yet. **S3 must add the guard** that refuses the change once the item has a `stock_moves` row, and S4 the same for a `sale_lines` row. This is listed in *Hands off* rather than pretended away here.
- **Loading**: skeleton of the real field stack at the real control heights (§B.10.1). **Error**: field scope for validation, region scope for a rejected save (§B.10.3). **Denied**: a `cashier` sees the panel read-only with no footer, and without cost — `supplier_items.cost` and `service_cost` are purchasing figures and are not on a counter person's screen.

### The three settings sections

A new **`Catálogo`** group in the settings dialog's rail, beside Organización, Operación and Registros (§B.8.4·4). This is an addition to that section's list and the design system is where the diff lands. All three are Compact density, 40px rows, with component-local filter and pagination state, because a dialog does not own the address bar it floats over (§B.4.1, §B.8.4·4).

- **`Laboratorios y categorías`** — two blocks in one scrolling pane under `t-16`/500 titles. Laboratorios: `Laboratorio 46 · NIT 24 · Referencias 30 (right)`. Categorías: an indented two-level list with the same reference count. The count is a link into the catalog grid pre-filtered, so a reference list is never a dead end.
- **`Proveedores`** — `Proveedor 30 · NIT 16 · Contacto 20 · Plazo de pago 14 · Días de entrega 12 (right) · Referencias 8 (right)`. A supplier's detail is a form in the same pane, not a record panel. Its item list is **read-only here** and links into the catalog grid; the writer of a `supplier_items` row is the item editor, so one row has one editor. **If the pilot's buyer lives in this screen daily it earns a route under Compras at S6**, and that is the diff.
- **`Clientes`** — server-paginated, `q` over document and name, `Cliente 30 · Documento 20 · Teléfono 16 · Correo 20 · Consentimiento 14`. It is a settings section rather than a route because §12 rules out a CRM, a loyalty programme and a patient record: `customers` exists to identify the acquirer on a fiscal document and to recognise a returning customer, and the till creates them inline at S4.
- **Loading, empty, error, denied**: as above; `owner` and `admin` only, and a `cashier` reaches no settings section at all (§B.8.3).

## Offline

**Every surface in this stage is an office surface, and every one of them is online-only.** That is the correct answer and not a gap: the office read model is server-authoritative over the whole network and is never synced into a browser (§4, A4). The catalog grid, the item editor and the three settings sections are read by an `owner` or an `admin` at a desk, over the network, one server-paginated page per interaction.

**What they show with no network** is §B.10.3's route-scope error inside the content region: the operation named, the entity named, a `Reintentar`, and a selectable correlation id. They do **not** fall back to a cached page, and the service worker never caches an API response — two caches would be two truths, and a cached catalog page is a price a cashier could read and act on (§9, §5 rule 1). No surface in this stage renders `SyncStatus`, because no surface in this stage reads the local store (§B.9.1); the Catálogo filter bar's right slot carries a live count instead.

**The `cashier`'s read-only Catálogo is online-only too, and this is a trade taken with open eyes.** A cashier who loses the network cannot look up whether the network stocks something outside their own sede. They can still sell everything their own sede has, at the right prices, because that is S2's local store and S4's till, and it is a different surface built to a different contract (§4, §5 rule 2). A catalog lookup is an office question asked from a counter, and answering it from a stale local copy without saying so is exactly what §5 rule 1 forbids.

**Neither command has an offline dimension.** The load tool and the demo seed both run on the server, inside a pinned transaction — one per entity file for the loader, one for the whole run for the seed — against the same database everything else reads.

**What this stage owes the offline contract is data, not behaviour.** S2 syncs one sede's operating set (A4), and the catalog is most of it: the item's identity, its price, its `vat_class`, its `tracks_stock`, `tracks_lots` and `splittable` switches, and every one of its barcodes — because a scan at a till must resolve locally, in under 50ms, with no network in the path (§4). The row-count estimate and the scoping predicate are S2's to declare under ledger rule 9, and the one thing worth flagging now is that **the catalog's predicate is the tenant, not the location**: a network's catalog is one catalog (§2), so a device pulls the tenant's active items and not a sede-filtered subset. That is a few thousand rows and a few megabytes, which is what §4 budgets a till to hold.

## Acceptance

Each of these passes or fails while somebody watches a screen.

1. A `Toma de presión` is created as a service with `tracks_stock = false`, a price and a `vat_class`, in the same editor as a box of pills — and that editor rendered no laboratorio, no presentación, no registro sanitario, no fraccionamiento and no proveedores section for it. It then appears on the same grid, in the same catalog combobox, and under the same filters (A7).
2. Attempting to set `tracks_lots` on that service is refused by the database, not by the form alone.
3. An item is created with `splittable = true`, `unit = 'tableta'`, `units_per_pack = 30` and a price of `$416,67`. The editor shows the derived box price as `$12.500`. A three-unit line totals **`$1.250`**, and the demonstration shows that rounding happened once, at the total.
4. A second barcode and a third are added to one item; exactly one is `Principal`; adding a code that already belongs to another item is refused with a message naming that item.
5. The search field finds one item from four thousand by its name, by its laboratorio, by any one of its three barcodes and by its INVIMA registration number, from the same box, and the grid's server time stays **under 400ms p95 on any filter combination** (§4).
6. Filtering `Registro INVIMA · Registro vencido` returns exactly the items whose `invima_status` is `expired`, the badge reads **Registro vencido** on the critical tint with a solid dot, and none of those rows is disabled or unsellable (§B.7.4, §12).
7. An item whose `invima_expires_at` passed yesterday is `valid` before the sweep runs and `expired` after; an item at `in_process` and an item at `not_applicable` are untouched; running the sweep twice on the same day changes nothing.
8. An administrator sets an item's `invima_status` by hand and the change lands an `audit_log` row carrying the actor, the entity and both before and after — which is what makes "what was its registration state on the day we sold it" answerable.
9. A price change creates a new `item_prices` row and closes the previous one in the same transaction; the previous row is still readable with its window and its `source`; no endpoint edits a row that has been in force.
10. A price scoped to Chapinero applies at Chapinero on the same date that the network-wide price applies at Kennedy, and removing the Chapinero row returns Chapinero to the network price with no further edit.
11. A price row dated a week ahead is created, is not in force today, and is in force on its date with no job having run.
12. The load tool refuses to start without an explicit tenant argument; it is a dry run unless `--apply` is passed; it writes an `imports` row either way; and running the same file a second time creates nothing, updates nothing and reports it.
13. The load tool fails a row that carries no `vat_class`, names the count in its summary, and exits non-zero. Re-run with `--vat-class-when-missing=excluded`, the run succeeds and the `imports` row records how many rows took the operator's value — so the guess is a recorded decision, not a silent default.
14. The load tool run while pinned to tenant A writes nothing into tenant B, and a row referencing a manufacturer that belongs to another tenant fails that row rather than reaching across (ledger rule 6, A1).
15. `seed_demo_tenant` run against an empty tenant produces **Droguerías La 45** with its six sedes, the seven laboratorios, Coopidrogas among four proveedores and **4.284 items of which five are services** — and the catalog grid's footer reads `1-15 de 4.284` with the pagination ending at **172**, which is the drawn screen reached by one command and nothing typed by hand.
16. The fifteen products the Existencias screen names exist with the laboratorio drawn beside each, the eleven the Compras screen orders exist, and the three Mostrador ticket lines price to `$3.900`, `$2.600` and `$5.200` for a total of **`$15.600`** (§A.11).
17. The seed run a second time against the same tenant changes nothing and every id is the one it had before. Run against a tenant holding a single hand-created item it is **refused before any write**, naming the table and the count, and `--reset` is refused by the same check.
18. A fixture registered against the registry runs after the fixture it declares a dependency on, and one that raises leaves the tenant exactly as it was — not half seeded.
19. A `cashier` signs in, reaches Catálogo read-only, and sees no `Nuevo producto`, no editor footer and no settings sections — not one of them rendered disabled (§B.8.3). Costs are absent from every response they receive.
20. There is no way to hard delete an item from any surface or endpoint. Deactivating one removes it from the default grid and from the combobox and leaves it readable by id.
21. Deleting a laboratorio that four hundred items reference is refused with a message naming the count and what to do instead.
22. A customer no sale references is deleted outright. A customer a sale references is erased instead: the row and the sale's `customer_id` survive, name and document come back empty, the `Clientes` list reads `Cliente eliminado`, the response named the sale count, and `audit_log` carries the actor with before and after — and no `sales` row was touched.
23. A tenant with no items shows the never-populated empty state with a working primary; a filter matching nothing shows the filtered state with a secondary `Quitar filtros`; neither says `Sin datos` (§B.10.2).
24. Page, size, sort and every filter are in the URL, and pasting that URL into another browser reproduces the same view (§9, §B.4).
25. The grid answers `j`, `k`, `Enter`, `x`, `Esc` and `/`; every interactive element renders the one 2px `#0071e3` focus ring at a 2px offset; no surface renders a spinner (§B.13.2, §B.5.1, §B.10.1).
26. Every figure on every surface in this stage is `$15.600`, `4.284`, `24,8%` and `03/2027` — thousands dot, decimal comma, no currency decimals, `MM/AAAA` (§A.11).
27. Both tables and both tenants: two networks on one instance, each with its own catalog, and neither returns a row of the other's on any table this stage created, with RLS reported as both enabled and forced on every one of them.

## Hands off

- **`items`**, complete, with `type`, the three INVIMA columns, `vat_class`, `tracks_stock`, `tracks_lots`, `tracks_expiry`, `unit`, `splittable`, `units_per_pack`, `active`, `custom`, `service_cost` and `external_code`. S3 reads `tracks_stock` and `tracks_lots` to decide what a movement means; S4 reads `tracks_stock` to decide whether a line needs a lot; neither writes any of them (ledger, disputed columns).
- **The base-unit rule**: every quantity in the rest of this product — ledger moves, ticket lines, order lines, forecasts, reports — is expressed in `items.unit`, and `units_per_pack` is the only conversion, applied at receiving and at supplier cost.
- **`item_prices`** with `manual` and `imported` rows, the resolution rule (most specific scope, window containing the instant, latest `effective_from`), one open row per item per scope, and **`regulated_max_price` created empty for S7 alone**. A null cap means *unknown*, never *uncapped*, and saying which is S7's (ledger).
- **`item_barcodes`** with a code unique per tenant, so a scan resolves to exactly one item — which is what S2 syncs and S4 depends on at 50ms.
- **`manufacturers`, `categories`, `suppliers`, `supplier_items`** with `cost`, `min_order_pack` and `is_preferred` for S6 to read and to update from real receipts, and `suppliers.lead_time_days` for S6 to overwrite from observed lead time (ledger).
- **`customers`** for S4 to create at the counter and for S5 to read as the acquirer when it builds the canonical sale document. **The deletion rule is settled here and owes S5 nothing**: a customer any sale references is erased in place and never hard-deleted, and the acquirer is re-rendered from the current row on every send rather than snapshotted (*The Ley 1581 deletion*). **S3 and S4 must add the `tracks_stock` edit guard** once an item has moved or been sold.
- **`imports`** with its column set, for S6's sales-history loader to record its runs in without a migration (ledger).
- **The four enums**, and the `vat_class`-to-rate constant that S4 uses to compute per-line tax and S5 puts on a document.
- **The Inventario module and its route pattern.** `Catálogo` is the module's landing route until S3 ships `Existencias` and takes it; the segmented control is the switch.
- **The catalog combobox** — searchable, over thousands of rows, keyboard-complete per §B.5.4 — used by S3's receiving, S4's product lookup, S6's order lines and S7's price grid.
- **The candidate set for S2's sync registry** (ledger rule 9, S2 to declare): `items`, `item_barcodes` and `item_prices` are required at the till; `manufacturers` and `categories` are required for display. **Their scoping predicate is the tenant, not the location** — the catalog is network-wide (§2) — and the row-count estimate per device is the tenant's active item count, roughly 4.284 in the handoff data, plus its barcodes and its open price rows. S2 owns the registry, the cursor index `(tenant_id, location_id, updated_at, id)` and the exception these tables represent to its `location_id` shape (ledger rule 4).
- **The demo seed command and its fixture registry** — `seed_demo_tenant`, an explicit tenant argument, one transaction, a topological order over declared `requires`, `guard_tables` per fixture, and the two invariants above. **Every later stage registers its own fixture** — S3's stock and lots, S4's shifts and sales, S6's orders and forecasts, S7's proposals, S8's assistant traffic, S9's rollups, S10's checklist entries — **and is not finished until its screens render convincingly from the seed** (§1, ledger). The catalog fixture is the root the others declare a dependency on, and the drawn rows in it are literal: a later fixture hangs its lote, its vencimiento, its cantidad or its venta on an item that already exists rather than inventing a sixteenth product.
- **The one job pattern for this chain**: a per-tenant cron payload that pins before touching anything, an idempotency key of `(tenant_id, run_date)`, and set-based work that repairs a missed run rather than losing it.

## Gated on

- **§11.2 — what the fifteen-year-old system can export, which is a quality variable and not a gate** (§1, *Cold start*; §11.2's own row now says the same). **The load tool handles a partial export by design.** A client who can export a catalog but no history, or barcodes but no lots, or a product name with the laboratorio buried inside it, is the **normal case rather than the degraded one**, and every missing piece has an answer rather than a blocked run:
  - **No stable product code** → `items.external_code` lands null and idempotency falls back to the primary barcode. Where an export carries neither, a second run refuses those rows rather than silently doubling the catalog — the failure is loud, which is the whole reason the fallback is named rather than improvised.
  - **No IVA class** → the row fails until an operator passes `--vat-class-when-missing`, and the `imports` row records how many rows took it. This is the one field the loader will not infer, because a wrong `vat_class` is a tax error that compounds per line.
  - **Laboratorio and categoría as free text inside the product name** → the throwaway translation script splits them. What reaches the loader is always our own documented CSV contract, so a wrong guess about the export costs that script and never the loader — which is why this stage can be built before the question is answered.
  - **No barcodes** → items load without them and the counter searches by name until the droguería prints its own codes. S2 syncs an empty barcode set without complaint, and `item_barcodes` fills later without a migration.
  - **No lots, no expiry, no opening stock** → out of scope here in any case: opening stock is a counted fact entered through S3's ledger service, never an import (§6, ledger).
  - **Prices per pack rather than per unit** → converted by `units_per_pack` at load, the same conversion receiving and supplier cost use (the base-unit rule). An export that cannot say which of the two it means is the one case worth a phone call before the run rather than after it.
  - **No sales history at all** → S6's loader has nothing to do and nothing waits on it. The forecast runs **parametric** off the sede's own `stock_policies` and elasticity **withholds per item** rather than inventing a number, which is the regime §1 fixes and the `Confianza del modelo` chip states on screen.

  **An instance with no export at all is fully functional.** The demo seed fills every screen and every model runs in a regime it names, so the product is demonstrable and pilotable before one legacy row is read. What a good export changes is how fast the forecast becomes good and how early elasticity appears per item — **free accuracy, worth chasing early, and never worth blocking a pilot for**.

- **§11.6 — who at the client owns the catalog cleanup**, which the architecture already calls the usual schedule risk. It blocks neither writing this stage nor finishing it — the seed is what the screens are demonstrated from, and the completion test is that they render convincingly from it (§1). What it still blocks is **proving the loader against the client's own catalog**, which is the only place duplicate rows show up, and they are refused by `UNIQUE (tenant_id, name, presentation)` by design. That proof is onboarding work on a real export, not a stage gate.
- **Names coined here, because neither the architecture nor the ledger supplies them.** `items.external_code`, `items.service_cost`, `supplier_items.is_preferred`, `customers.data_consent_at`, and the whole column set of `imports` (`kind`, `source`, `status`, `dry_run`, `started_at`, `finished_at`, `rows_read`, `rows_created`, `rows_updated`, `rows_failed`, `errors`, `started_by_user_id`) — §3 names that table and no column of it. Each is justified where it is defined. `imports.kind` and `imports.status` are deliberately **text and not Postgres enums**, so the seed writes `demo_seed` and S6 adds `sales_history` without migrating a type S1 owns; the cost is no database-level guard against a typo, which the loader's own validation covers.
- **`Cargar catálogo` has no owner.** Design-system §B.8.4·7 fixes that string as the primary action of a newly provisioned tenant's empty state, which implies a tenant-facing import wizard. §12 does not name one, no stage in §13 delivers one, and the deck's rollout has us loading the catalog during onboarding. S1 ships `Nuevo producto` with an explanatory line instead. **If a wizard is wanted it is a stage with an owner**, not a button, and the difference matters because file upload, mapping, preview, partial failure and re-run are the whole of it.
- **The box-versus-unit price question.** The decision taken above — one price per base unit, box price derived — is wrong the moment a network wants a box priced below `units_per_pack ×` the unit price. It is answerable by one query against the pilot's price export and it must be answered **before S4**, because after that a till is selling on it.
- **Whether a `cashier` sees price in the read-only Catálogo.** S1 decided yes for price and no for cost. Design-system §B.17·3 records the wider per-action question — what a `cashier` sees on a read-only Inventario, and whether they may act on it — as still open and blocking S3 and S7. S1's answer is the narrow one and is the diff to apply if the wider decision goes the other way.
- **Whether the acquirer vocabulary is complete — not how it is spelled.** How it is spelled is settled: S1 stores the domestic codes at rest and S5's per-target mapping translates them for whatever system the client invoices with (§8, §11.1), so a target that names them differently is a mapping and never a migration on `customers`. The open question is the narrow one — whether some client's system distinguishes an acquirer type our seven codes do not draw at all. **What breaks if we are wrong**: it is a value nobody ever captured, so the fix is a change to this table, to the customer form and to S4's identification step, and the rows already taken cannot be repaired from the data — nobody knows which `CC` should have been something else, so the repair is asking each customer again at the counter. **Where it is discovered**: the first mapping written against a real client's API, which is the earliest anyone sees the far end's field list.
