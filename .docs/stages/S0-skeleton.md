---
stage: S0
title: Skeleton
depends_on: []
blocks: [S1]
source: "architecture.md §1, §2, §3, §4, §5, §9, §10, §11, §12, §13; amendments A1, A2, A10; ownership.md rules 1–6 and 8, the settings register and the cross-stage services table; design-system.md Part A and Part B"
---

# S0 — Skeleton

## Outcome

The platform runs as four containers against one Postgres, and a person can sign in to it. A platform admin creates a droguería network, its sedes and its first `owner` from Django admin; that owner invites people by email, gives each of them one of four roles and — for a cashier — a home sede, and reads an append-only record of every mutation an elevated role made. Two networks sit on one instance and neither can see the other's rows, because the database refuses to return them rather than because the application remembered to filter.

What a signed-in person sees is the finished frame: the 280px sidebar with `Droguerías La 45` in its header, the seven nav items in the drawn order, the version stamp in Geist Mono at the foot of the nav block, and the user footer reading `Marcela Ríos · Administradora`; the 64px page header with its breadcrumb and its one `t-28` title; and the content region. Seven routes exist behind that nav, one per later-stage surface, each rendering an empty state that names what will live there and what has to happen first. **S0 fills none of them.** A cashier signing in sees two of the seven, lands on Mostrador, and finds the same honest empty state there.

Underneath, three things exist that every later stage calls rather than rebuilds: the tenant-pinning path for all five contexts of ledger rule 6 — HTTP request, management command, background job, the sync push S2 will call, and the unauthenticated inbound path, which no stage calls at v1 — plus the one deliberately unpinned resolution path that maps an identity or an external key to a tenant id and returns nothing else; the shared location-scoping predicate that confines a cashier to their sede and defaults the office to the whole network (A2); and the audit write path. Nothing later in the chain issues its own `SET LOCAL`, writes its own `location_id IN (…)`, or appends to `audit_log` by hand.

And the component layer exists. Nothing in this repository ships a button, so this stage authors the whole of design-system.md Part B once — the eight-step type scale, four surface levels, the closed spacing scale, the table with its four density modes and its row states, every form control and the single focus ring, the four button variants at four sizes, the five status families with their solid and hollow dots, and the skeleton, empty and error treatments — against the tokens Part A transcribes. The grid contract is a primitive here, bound to no domain data, proven on the one server-paginated list S0 actually has.

## Inherits

Nothing. This is the first stage. Everything below is created here or is deliberately deferred.

Two things worth stating anyway, because they read like inheritance and are not: **the ELOS v2 architecture** (`internal/US-2GZOD/dev-elos/.docs/architecture.md`) supplies the tenancy model, the security posture, the stack, the job runner and the admin surface, and architecture.md §0 adopts them wholesale — they are settled and are not re-argued here, but no ELOS code is imported and no ELOS table is reused. And **`../handoff/`** supplies four finished screens whose tokens, densities and component specifications are already transcribed into design-system.md; this stage builds from the transcription, not from the prototype's inline styles (design-system.md, *What was read*).

## Scope

### In

1. **One repository and one compose file** bringing up four services — `web` (Django 6.1 + django-ninja 1.5.x, serving the built React assets), `worker` (the same image, running `procrastinate worker`), `postgres` (18), and `caddy` (TLS, auto-renew) (§9). No Redis and no pgbouncer, decided in §4 on Botica's own numbers and not inherited on authority.
2. **Two Postgres roles.** A migration role that owns every table, and a runtime role the Django process connects as, which owns nothing (A1). A table's owner bypasses its own RLS, so the runtime role never being an owner is the precondition on which every policy in this document rests.
3. **The migration baseline and the table convention** every later stage follows without restating it: `id` (uuid), `tenant_id`, `created_at`, `updated_at`, an RLS policy `USING (tenant_id = current_setting('app.tenant_id')::uuid)`, and `FORCE ROW LEVEL SECURITY` (§3, A1). Stage documents after this one list only what departs from it.
4. **Tenant pinning for all five contexts** of ledger rule 6, shipped as one module with five entry points, plus the single permitted unpinned resolution path. Detailed below under *The pinning path*.
5. **The shared location-scoping predicate** (A2, ledger, cross-stage services). Sede visibility is query-layer and UI-default, never RLS. Detailed below under *Location scoping*.
6. **`tenants`, `locations`, `users`, `invitations`, `audit_log`**, the `role` and `location_type` enums, the `settings` JSONB column and its `tenant` key group (ledger).
7. **Invite-based authentication** on django-allauth headless (§9). A `users` row is created only by consuming an invitation (§3). **No self-signup path exists**, and no registration endpoint is exposed even behind a flag.
8. **Four roles, one enum, one dependency** (§2): `platform_admin`, `owner`, `admin`, `cashier`. Every endpoint in the product runs behind that single dependency, inside the pinned transaction. No policy engine, no per-object ACLs.
9. **Django admin as the platform-admin surface** (§2, §9), hosting the `tenants` CRUD, the `locations` CRUD and first-`owner` creation (ledger). It gets no styling budget and nothing in it is ever shown to a tenant user (design-system §B.8.4·7).
10. **The audit write path** — actor, action, entity, before/after — and its reader. S0 ships the path and writes through it for its own elevated-role mutations; every later stage appends through the same path on its own (ledger, cross-stage services).
11. **Procrastinate 3.9's Postgres-backed queue**, installed by the migration role, with the `worker` service running against it (§9). One job is defined here (see *Jobs*), so the pinning-in-a-job contract is exercised rather than asserted.
12. **The transactional email adapter** (§10) — invitations at S0, expiry alerts and scheduled reports later. Delivery failure never blocks the operation that triggered it.
13. **Django native psycopg3 pooling** (`OPTIONS: {"pool": True}`) (§9). `SET search_path` per tenant is prohibited — it is schema-per-tenant in disguise — and `SET LOCAL app.tenant_id` is transaction-scoped, so request-level transactions must be on for the pin to hold at all (§9, A1).
14. **The django-ninja API with `/api/openapi.json`**, and the typed frontend client generated from it with `openapi-typescript` + `openapi-fetch` (§9). Every application path carries the `/api/` prefix and every path segment is English (§1, ledger naming).
15. **The design-system Part B component layer, authored once.** The type scale (§B.1), surface levels (§B.2), spacing and insets (§B.3), the table with its four density modes, header, row states, columns, footer and bulk bar (§B.4), the focus ring and every form control including the quantity stepper (§B.5), the four button variants at four sizes with their busy and disabled treatments (§B.6), the five status families, the solid/hollow rule and the badge (§B.7), the shell primitives (§B.8), loading, empty and error (§B.10), the counter-density tokens and props (§B.11), the four chart forms (§B.12), the keyboard layer (§B.13), the motion budget (§B.14), and the token set of §B.15. Conformance is design-system.md §B.16.
16. **The single number and date formatter** (§A.11). One module, one locale, no `i18n` runtime, and no call site calling `toLocaleString` on its own. Thousands dot, decimal comma, `$` prefixed unspaced with no decimals, `M` above a million behind a non-breaking space, U+2212 for a negative, `MM/AAAA` for a lot expiry.
17. **The application shell**, exactly as §A.13 draws it and §B.8 extends it: the 280px L0 sidebar with the organisation header, the flat seven-item nav with its counter slot, the version stamp and the user footer; the 64px sticky header with breadcrumb and one `t-28` title; the L1 content region at a `32px 40px` inset; the 52px filter-bar slot on server-paginated routes; the 440px record-panel slot; and the settings dialog (§B.8.4·4) with the three sections S0 owns.
18. **One empty, role-gated route per later-stage surface** — Panel, Inventario, Compras, Precios, Mostrador, Sedes, Reportes — plus the role-based landing redirect.
19. **The grid contract as a primitive** (§9, §B.4): `manualPagination`, `manualSorting`, `manualFiltering`, `rowCount` from the API, page/size/sort/filter state in typed search params on a route and component-local inside the settings dialog (§B.8.4·4). Bound to no domain data at S0 and proven on the audit log.
20. **The PWA application shell and its service worker** (Workbox, §9). It precaches the built shell and the self-hosted font faces and **never caches an API response** — the local store is the offline data layer and two caches are two truths.
21. **Vendor and infrastructure secrets read from the instance's own store**, never held centrally. Provisioning them is deploy automation, not application code.

### Out

- **Every domain table.** `items`, `item_barcodes`, `manufacturers`, `categories`, `suppliers`, `supplier_items`, `item_prices`, `customers`, `imports`, the load tool and the catalog grid — **S1** (ledger).
- **`devices`, `sync_conflicts`, the local store, `/api/sync/pull` and `/api/sync/push`'s payloads, the sync registry, the idempotent-client-write helper, the sync-state UI and the `navigator.storage.persist()` request and its display** — **S2** (ledger, A4, A5). S0 ships the push endpoint's *transaction and pinning semantics* (ledger rule 6, context four) and nothing else about it. The `SyncStatus` component of §B.9.1 is S2's; S0 ships the filter bar's right-hand provenance slot it renders into, empty.
- **`lots`, `stock_moves`, `stock_on_hand`, `stock_policies`, `transfers`, `stock_counts`, the ledger service, the Existencias screen and the stock-state derivation of §B.7.4** — **S3** (ledger rule 7, A3).
- **`shifts`, `sales`, `sale_lines`, `payments`, `sale_returns`, the till, the payment flow and every counter-density surface** — **S4**. S0 ships the counter-density *tokens and props* (§B.11, §B.15) because retrofitting a density mode into a component library four stages later is a rewrite; it ships no surface that selects it.
- **`fiscal_documents` and the sale-handoff service** — **S5** (A9). Botica issues no fiscal document: it hands a canonical sale to whatever system the client already invoices with, and a proveedor tecnológico is one more target with one more mapping rather than an adapter of its own (§8). **`dian_resolutions` and `numbering_leases` are not built at v1 at all** (A6, deferred) — if the client's system issues, the client's system numbers, and the only number Botica allocates is S4's internal `sales.number`. S0 ships the unauthenticated-inbound pinning path and registers no lookup in it. **No stage calls it at v1** — S5 polls its target rather than being called back, and ships no callback endpoint (ledger rule 6). The path exists so that the first stage that needs one does not invent a second.
- **Purchasing** — **S6**. **Pricing** — **S7**. **The assistant** — **S8**. **`daily_metrics`, the Panel, Sedes' content and Reportes** — **S9**. **Compliance, the document vault, tenant and sede provisioning as a flow, backups, observability and the runbook** — **S10** (ledger). S0 creates `locations` and writes rows into it through Django admin; three stages split what sits on top of it — the **devices** half of the tenant-facing **Sedes y dispositivos** settings section is **S2**'s (its scope item 19 builds it), the **sedes** half is **S10**'s, and the `/locations` route's content is **S9**'s.
- **Every `tenants.settings` key group except `tenant`** (ledger rule 5, settings register). `sync` — S2. `inventory` — S3. `invoicing` — S5. `purchasing` — S6. `pricing` — S7. `assistant` — S8. `compliance` — S10. Each lands empty here and is written by its owner through its own `GET`/`PATCH /api/settings/{group}` pair on the same helper.
- **The command palette's contents.** `⌘K` is reserved and bound to nothing in v1 (§B.13.2). Reserving it costs nothing; binding it to the search field as a placeholder costs retraining.
- **Responsive behaviour below 1440px.** Design-system §B.17·2 leaves it open and §B.11 fixes a 1280 × 720 floor for the counter. S0 builds the shell at the drawn width and does not guess the collapse order.
- **A `memberships(user, tenant, role)` table.** §3 puts `role` and `tenant_id` directly on `users` and the ledger resolves for §3. One person belongs to one network; the join table arrives the day someone belongs to two, and nothing else changes when it does.
- **A dark theme, white-label theming, SSO, per-object permissions, a custom-fields UI, a workflow builder** (§12, design-system *Reference standard*). Do not scaffold a `dark:` variant.
- **Redis and pgbouncer** (§4, §9).

## Data

Every table below carries the §3 convention — `id`, `tenant_id`, `created_at`, `updated_at`, RLS enabled **and** forced. This stage establishes it; every later stage assumes it and lists only its departures.

| Table | Change | Stage-specific detail |
|---|---|---|
| — | add enum | `role`: `platform_admin` \| `owner` \| `admin` \| `cashier` (ledger enums) |
| — | add enum | `location_type`: `store` \| `warehouse` \| `distribution_center` (ledger enums, A10). Nothing in v1 creates a `distribution_center`; the value exists from day one so serving distributors is a configuration rather than a migration |
| `tenants` | create | `name`, `slug`, `nit`, `status`, `settings` JSONB (§3). **Its RLS policy is `USING (id = current_setting('app.tenant_id')::uuid)`** — the table has no `tenant_id`, it *is* the tenant, and a policy written against a column that is not there is a policy that silently allows everything |
| `locations` | create | `code`, `name`, `type` (`location_type`), `address`, `city`, `phone`, `status` (§3). A **sede**. `UNIQUE (tenant_id, code)` |
| `users` | create | `email`, `name`, `role`, `tenant_id` **nullable** (null for `platform_admin`), `location_id` **nullable**, `status`, `last_login_at`, `platform_admin` (§3). `UNIQUE (tenant_id, email)`. `location_id` is the home **sede**; for `owner` and `admin` null means all locations (A2, ledger), and for a `cashier` it is required — see the constraint below |
| `users` | add constraint | `CHECK (role <> 'cashier' OR location_id IS NOT NULL)`. A cashier with no home sede is a cashier who cannot open Mostrador and whose failure is unattributable at a counter |
| `invitations` | create | `email`, `role`, `location_id` nullable, `token_hash`, `invited_by_user_id`, `expires_at`, `status`, `accepted_at`, `revoked_at`, `last_delivery_error`. **Columns coined here** — §3 fixes the behaviour (creation is invite-only) and enumerates no columns. `token_hash` only: the plaintext token exists in the email and nowhere else. Partial `UNIQUE (tenant_id, email) WHERE status = 'pending'` — two live invitations to one address is an ambiguity nobody can resolve from the roster |
| `invitations` | add constraint | The same cashier check as `users`: an invitation at role `cashier` carries a `location_id`, at `owner`/`admin` it does not |
| `audit_log` | create | `actor_user_id`, `actor_email`, `action`, `entity_type`, `entity_id`, `before` JSONB, `after` JSONB, `request_id` (§3). `actor_email` and `request_id` are **coined**: the first because `owner` may hard delete a user and a mutation attributed to nobody is not a record, the second because §B.10.3 requires a correlation id on every route-scope error and a mutation nobody can trace to a report is half a trail |
| `audit_log` | add grant | The runtime role holds `INSERT` and `SELECT` and **not** `UPDATE` or `DELETE` on this table. Append-only enforced by the database, not by the discipline of eleven stage documents |
| `audit_log` | add index | `(tenant_id, created_at DESC)` — the reader's only ordering (ledger rule 4) |
| `audit_log` | fix vocabulary | `action` and `entity_type` are a closed vocabulary, not free text — see below |
| all of the above | add policy | RLS enabled and forced; the tenant predicate above (§2, A1) |
| Procrastinate schema | create | The queue's own objects, installed by the migration role so the runtime role owns none of them (§9, A1) |

**Status values are checked text, not Postgres enum types.** The ledger names exactly two enums for S0, `role` and `location_type`, and does not name a status enum; the statuses are therefore constrained columns whose allowed values are declared once and shared with the API schema so the typed client gets a literal union. `tenants.status`: `active` \| `suspended`. `users.status`: `active` \| `suspended`. `locations.status`: `active` \| `closed`. `invitations.status`: `pending` \| `accepted` \| `revoked`; **`expired` is derived from `expires_at` and is never stored**, because a stored expiry is a second clock that has to be swept. Interface labels: **Activa** / **Suspendida** for a network, **Activo** / **Suspendido** for a person, **Activa** / **Cerrada** for a sede, **Pendiente** / **Aceptada** / **Revocada** / **Vencida** for an invitation. *If this is wrong* — if a pilot needs a sede in `en montaje` before it opens — it is a `CHECK` change and a label, not a type migration; that cheapness is the reason for the choice.

**The audit vocabulary.** `action` is `create` \| `update` \| `delete` \| `archive` \| `approve` \| `reject` \| `send` \| `revoke` \| `impersonate`, and `entity_type` is the English table name the row is about. Ten stages append to this table (ledger), and a free-text `action` produces `role_changed`, `changed_role` and `ROLE_CHANGE` in one column within a year — at which point the trail can be read by a person and not by a query. A stage needing an eleventh verb adds it here, in this table, rather than in its own migration. The eight rows S0 writes are: `create`/`invitations`, `send`/`invitations` (the resend), `revoke`/`invitations`, `create`/`users` (an acceptance — the actor is the invitee), `update`/`users` (role, status or home sede, with the changed field in `before`/`after`), `delete`/`users`, and `update`/`tenants` (the settings `PATCH`). `before` is null on a create and `after` is null on a delete; both being null is a defect, not an economy.

**`tenants.settings` and the `tenant` key group.** S0 creates the column and writes exactly one group (ledger rule 5). The group carries `legal_name` (the razón social S5 puts on the canonical sale document it hands to the client's invoicing system), `timezone` (default `America/Bogota`), `currency` and `number_format`. It does **not** carry the NIT: §3 gives `tenants` a `nit` column, and where the ledger and the architecture disagree about *what a thing is* the architecture wins (ledger preamble). Duplicating it into JSON would give the product two NITs that can differ. `timezone` is stored rather than hardcoded because S9's `daily_metrics` needs a day boundary and a rollup whose day is defined in code cannot be re-cut. `currency` and `number_format` are stored and rendered **read-only**: §1 fixes one locale and §A.11 makes the formatter a constant.

Every group in the column — this one and the seven that land empty — is written through **one helper that issues a single `jsonb_set` per group**, leaves every other group as it stands, and **raises rather than returning quietly when the `UPDATE` matches no row** (ledger rule 5). Under RLS a write against the wrong pin updates nothing silently, and a `200` on a write that touched no row tells an owner their margin goal was saved when it was not. Within a group, last writer wins.

### The pinning path

Ledger rule 6 names five contexts and gives S0 all of them. One module, five entry points, and no later stage issues `SET LOCAL` itself.

| Context | What S0 ships | First caller |
|---|---|---|
| HTTP request | Middleware opens the transaction and issues `SET LOCAL app.tenant_id` from the session before any query runs. A request that resolves no tenant runs with no pin and therefore reads zero rows — not another tenant's | every stage |
| Management command | A base command that takes the tenant as a **required explicit argument**, resolves it, and pins before doing any work. There is no "current tenant" for a shell | S1's load tool, S6's history loader, S10's provisioning |
| Background job | A job wrapper that reads `tenant_id` from the payload, opens its transaction and pins before touching anything. S0's own invitation-email job runs through it, so the contract is exercised at S0 rather than first attempted at S5 | S5–S9 |
| **Sync push** | The endpoint's transaction and pinning semantics: the device's session resolves tenant **and** location, the whole batch applies inside **one** pinned transaction, and a row naming another tenant **rejects the batch** rather than being filtered out of it. S2 defines the batch format and the device identity; the rule that a foreign row is a rejected batch is fixed here | S2, and every stage a till writes to |
| Unauthenticated inbound | A resolution-then-pin wrapper: the tenant is resolved first through the unpinned path below, pinned second, and the handler body runs entirely inside the pin | **Nobody at v1** (ledger rule 6). Built so the first stage that needs one does not invent a second |

**The one permitted unpinned query.** Ledger rule 6 allows exactly one, and it is a **registry**: a stage registers a lookup that maps one opaque external key to one tenant id, and S0's module is the only place any of them runs. The registry's return type is a uuid — not a row, not a model instance, not a queryset — so the exception cannot widen by accident. S0 registers one lookup itself: sign-in, which maps an email to a user id and a tenant id. S2 will register the device key; S5 will register the provider's document reference. *If the return type is ever relaxed to a row*, the single audited hole becomes an unpinned read path into a tenant table, and every guarantee in this document goes with it.

**Identity is carried by the session, not re-read across the pin.** Sign-in resolves `user_id`, `tenant_id` (nullable), `role` and `platform_admin` once, through that unpinned lookup, and stores them in the session. On every subsequent request the permission dependency re-reads the acting user's row **inside the pin**, so a suspension or a role change takes effect on the next request rather than at the next sign-in. `platform_admin` is the single exception: their `users` row carries a null `tenant_id` and is invisible under any pin, which is correct — they are not a member of the network they are standing in — so their identity comes from the session and their reach is governed by which tenant they selected. *If `/api/me` read `users` under the pin for everyone*, a platform admin would 404 on their own identity the instant they selected a tenant.

**A `platform_admin` with no selection pins nothing.** There is no null-tenant pin and no wildcard. Django admin requires a tenant selection before any tenant-scoped model list renders, the selection lives in the session, and it is pinned per request like any other. Cross-tenant reads do not exist at any layer. *If a fleet-wide operational view is ever needed*, it is a separate reporting path with its own role and its own justification, and it is S10's problem — not a relaxed pin here.

### Location scoping

A2 is the whole rule: **the tenant is the security boundary, the sede is a scope.** An owner comparing six sedes reads all six in one query, and a policy that made that impossible would be worked around within a week. S0 ships one predicate helper and the cross-stage services table forbids a second (ledger).

The helper takes the acting identity and an optional explicit filter, and returns the set of `location_id`s a query may read. Two modes, chosen by the endpoint, never by the caller:

- **Scoped** — the default, and the only mode for a write. `cashier` → exactly `{users.location_id}`. `owner`, `admin`, and a `platform_admin` inside a pinned tenant → every `active` location in the tenant, narrowed by an explicit filter if one is present.
- **Network-read** — declared by an endpoint that is read-only and network-wide by design. §2 grants a cashier a network-wide stock lookup, and this is the mode that serves it. Every role sees every location; the UI still defaults its filter to the cashier's own sede (A2's "UI default" half).

**The helper raises rather than defaulting.** A `cashier` whose `location_id` is null is a misconfiguration, and the helper refuses the request instead of falling through to all locations. The `CHECK` constraint above makes that state unreachable through the API; the helper is what makes it unreachable through a management command or a bad backfill too. *If it defaulted*, a cashier would silently see every sede's till, which is exactly the thing §2 says must not happen — and it would present as a UI bug rather than as an error.

**A scoped query narrows; it never refuses.** An explicit filter naming a location outside the identity's set is **rejected**, not intersected away. A silently emptied result is indistinguishable from a sede with nothing in it, and the difference matters the first time a cashier reports that Suba has no stock.

## API surface

Two shape rules bind every stage, because one typed client is generated from one schema (§9): **every application path carries the `/api/` prefix and every segment is English** (§1, ledger naming) — `/admin` and the allauth headless surface are the two paths the architecture fixes elsewhere; and **a settings group is read with `GET /api/settings/{group}` and written with `PATCH /api/settings/{group}` against the pinned tenant, never a tenant addressed by id** (ledger). Both halves, always: the form that writes a group cannot render without reading it. S0's group is `tenant`.

| Method | Path | Purpose | Who may call it |
|---|---|---|---|
| GET | `/api/openapi.json` | Ninja's generated schema; the typed client is generated from it (§9) | Any caller |
| — | `/admin` | Django admin — the platform-admin surface. `tenants` CRUD, `locations` CRUD, first-`owner` creation, and record access in one selected tenant at a time (§2, §9, ledger) | `platform_admin` |
| POST / DELETE | allauth headless auth surface | Sign in, sign out, session. **No registration path is exposed** (§3, §9) | Anonymous to sign in; authenticated to sign out |
| GET | `/api/me` | The acting identity — name, role, `platform_admin`, the tenant (id, name, slug, status), the home **sede** or null, the location set the identity may read, and the running app version. What the shell gates its nav on (§2, §B.8.3) | Authenticated |
| GET | `/api/nav-counters` | The work-waiting count per nav item, as a map of route key to count and severity (§B.8.2). **Coined**, and it exists so seven stages do not each invent their own counter fetch. Empty at S0 | Authenticated |
| GET | `/api/locations` | The network's **sedes** — code, name, type, city, status. Read by the shell, by the scoping filter every later grid carries, and by the roster's sede select | `owner`, `admin`, `cashier` |
| GET | `/api/invitations` | Outstanding and recently resolved invitations for the pinned tenant | `owner`, `admin` |
| POST | `/api/invitations` | Invite an address at a role, with a `location_id` when the role is `cashier` | `owner` at any role below `platform_admin`; `admin` **at `cashier` only** — see below |
| POST | `/api/invitations/{id}/resend` | Re-send the same token. Does not rotate it | `owner`; `admin` for an invitation it could have issued |
| DELETE | `/api/invitations/{id}` | Revoke an outstanding invitation. The row stays, at `revoked` | `owner` |
| POST | `/api/invitations/preview` | The droguería, the invited address, the role and the sede a token names, so the accept screen can render before a password exists. **A `POST` for what is logically a read, because the lookup key is a credential** | Anonymous holder of a valid, unconsumed token |
| POST | `/api/invitations/accept` | Consume the invitation and create the `users` row (§3) | Anonymous holder of a valid, unconsumed token |
| GET | `/api/users` | The roster: name, email, role, home sede, status, `last_login_at` (§3) | `owner`, `admin` |
| PATCH | `/api/users/{id}` | Change status, home sede, or role. **Role change is `owner` only** (§2) | `owner`; `admin` for everything except role |
| DELETE | `/api/users/{id}` | Hard delete. "Delete" means hard delete (§2) | `owner` |
| GET | `/api/settings/tenant` | Name, slug, NIT, status and the `tenant` key group (§3, ledger) | `owner`, `admin` |
| PATCH | `/api/settings/tenant` | Edit the network's identity. `slug` and `status` are absent from the body | `owner`; `admin` excluding billing and API-key settings (§2) |
| GET | `/api/audit-log` | The append-only mutation trail, server-paginated (§3, ledger). S10's provisioning and impersonation entries land in the same table and read out here; S10 does not re-expose this endpoint | `owner`, `admin` |

**Who may invite, decided here.** §2 gives `admin` user management and withholds role changes, and an invitation necessarily carries a role — so issuing one at `owner` or `admin` is a role assignment by another name. **An `admin` may invite at `cashier` only; an `owner` may invite at any role below `platform_admin`.** *If this is wrong*, a network whose regente onboards a second administrator has to call the owner once, and the fix is one line in the permission dependency. The opposite error — letting an `admin` mint an `owner` — is a privilege escalation that no audit row undoes.

**`tenants.status` is enforced in the permission dependency, not in a view.** A network at `suspended` is unreachable by its own members; a `platform_admin` inside it is exempt, because restoring one is what that identity is for. `status` is therefore readable on `GET /api/settings/tenant` and absent from the `PATCH` — a network able to suspend itself would have no way back.

**The grid contract, as a wire shape.** §9 fixes the client half — `manualPagination`, `manualSorting`, `manualFiltering`, `rowCount` from the API — and S0 fixes the server half once, so eleven stages do not each invent a pagination envelope. Every server-paginated list endpoint accepts `page` (1-based), `page_size`, `sort` (a column key) and `order` (`asc` \| `desc`), plus its own typed filters, and answers `{ rows, row_count, page, page_size }`. **`row_count` is the count after filters and before pagination**, because it is the denominator of `1-15 de 4.284` and of the page group's width reservation (§B.4.5). It is never estimated and never omitted: until it arrives the range is a skeleton bar and the page group is not rendered — never `… de muchos`. An unknown sort key is a 422 rather than a silently ignored parameter, because a sort the server dropped looks exactly like a sort the data does not distinguish. `GET /api/audit-log` is the first endpoint on this shape and `GET /api/users` is the second.

**The invitation token travels in a request body or a URL fragment. Never in a path.** It is the credential the entire anonymous flow rests on, and **a path segment is written into every proxy and application access log by construction** — including the log line for the HTML request that merely loads the shell. The link an owner shares is therefore `{APP_URL}/accept#{token}`: a fragment is never put on the wire by any browser, so it reaches no request line, no access log and no `Referer`. The accept screen reads `location.hash`, clears it, and posts the token in a body. Two things back it up rather than one: `/accept/{token}` routes to a screen saying the shape is not used and neither previews nor accepts what is in it, and the access-log formatter scrubs that shape out of both the web server's line and Django's own log records, so a link already sitting in somebody's inbox cannot write itself down on arrival. Hashing the token at rest is a *different* fix for a different threat — it protects a stolen database, not a log — and S0 does both.

## Jobs

One job. The queue and the worker exist so later stages add jobs and nothing else, but shipping the chain with zero jobs means the first stage to need one is also the first to find out whether pinning inside a worker works.

| Job | Trigger | Idempotency key | Failure behaviour |
|---|---|---|---|
| `send_invitation_email` | An `invitations` row is created, or `POST /api/invitations/{id}/resend` is called | `invitation:{invitation_id}:{issued_at}` — a duplicate enqueue is a no-op; a resend after a revoke-and-reissue is a new invitation and therefore a new key | Retries with exponential backoff, five attempts. On exhaustion the row's `last_delivery_error` is stamped and the Personas section renders **Envío fallido** as a critical badge with `Reenviar` and `Copiar enlace` beside it. **The invitation stays valid.** Email is a delivery channel, not the credential, and an owner in a droguería will send the link over WhatsApp without being asked |

The payload carries `tenant_id`, and the job pins before touching anything (ledger rule 6, context three). The job never reads the plaintext token from the database — it cannot, only the hash is stored — so the token is passed in the payload and the payload is deleted with the job row on success. *If that is wrong* — if a queue's retention makes an unsent token readable for longer than the invitation's own life — the fix is to shorten `expires_at`, not to store the plaintext.

`LISTEN` requires a direct connection, so the day a transaction-mode pooler is introduced the worker's notification path breaks before anything else does (§9). It is not introduced here.

## UI

Design-system.md governs every surface below and its conformance checklist (§B.16) is the review. Four rules from it are binding on everything in this stage and are not restated per screen: **one focus-ring definition** and no bare `:focus` (§B.5.1); **geometry-matched skeletons and no spinner outside a button the user has already pressed** (§B.10.1, §B.6.2); **every empty state carries an action or explains why it does not, and never says `Sin datos`** (§B.10.2); and **every error names the operation, the entity and the recovery** (§B.10.3). Every string below is Spanish (Colombia) and the drawn ones are verbatim.

**The role labels, decided here.** `platform_admin` → **Plataforma**, `owner` → **Propietaria**, `admin` → **Administradora**, `cashier` → **Mostrador**. The handoff draws `Marcela Ríos · Administradora` and `Andrés Peña · Mostrador · Chapinero`, and design-system reproduces drawn strings verbatim; taking the feminine for the two gendered nouns matches the drawing rather than inventing a third convention beside it. *If the client rejects it*, it is one string table, and a per-user display preference is explicitly not built (§12's "no custom-fields UI" is the same instinct).

### The component layer

Nothing in this repository ships a component library, so this stage authors one. Each row below is built to its design-system section, at the geometry Part A transcribes, and is consumed unchanged by the stage in the last column. **A stage that finds a primitive missing amends this table in S0's document rather than authoring a second one beside it.**

| Component | Built to | First consumer |
|---|---|---|
| Type scale, the eight steps, and `tabular-nums` as the numeric default | §B.1, §A.2 | S0 |
| Surface levels L0–L3 and the nesting rule | §B.2, §A.7 | S0 |
| Button — `xs`/`sm`/`md`/`lg`, primary, secondary, ghost, destructive, disabled, busy | §B.6, §A.14 | S0 |
| Text input, textarea, select, **searchable combobox**, checkbox, radio group, label/help/error slot | §B.5, §A.15.2 | S0 (combobox first used by S1's catalog pickers) |
| Quantity stepper, desktop and counter geometry | §B.5.6, §A.18.2 | S6's `Sugerido` cell; S4's ticket quantity |
| Filter chip, active with its value pill and inactive | §A.15.1 | S3's Existencias filter bar |
| Segmented control | §A.15.3 | S9's period control |
| Status badge — five families, solid and hollow dots, and the dot-plus-label incidental form | §B.7, §A.16 | S0 (Personas), then every table |
| Table — four density modes, sticky header, the seven row states, column rules, footer, pagination, bulk bar | §B.4, §A.17 | S0 (Actividad, Personas) |
| Grid primitive — the client half of the contract, with a state adapter for route or dialog | §B.4, §9 | S0 (Actividad) |
| KPI card, including the reference-and-progress and badge variants | §A.19.1 | S6's order KPIs; S9's Panel |
| Section card | §A.19.2 | S4's Mostrador |
| Record panel, 440px, pushing and not trapping focus | §B.8.5 | S3 |
| Modal and confirmation dialog, focus-trapped, with the consequence in the button | §B.8.5, §B.6.2 | S0 (delete a person) |
| Toast | §B.10.3 | S0 |
| Skeleton, empty state (three kinds) and error state (five scopes) | §B.10 | S0 |
| Chart set — ranked bar list, column histogram, donut, progress with target | §B.12.5, §A.20 | S9 |
| Stock bar, rail plus its mandatory figure | §A.18.1 | S3 |
| Keyboard layer — `j`/`k`/`Enter`/`x`/`Esc`/`/`, the `g` sequences, `⌘,`, the `?` sheet, and `⌘K` bound to nothing | §B.13 | S0 |
| Counter-density tokens and the density prop on button, row and hit target | §B.11, §B.15 | S4 |

Two things the layer deliberately does **not** contain: the `SyncStatus` component of §B.9.1 and the staleness marker of §B.9.2, which are S2's and depend on a local store that does not exist here; and a density **toggle**, which §B.4.1 forbids — density is a property of a surface, decided in that surface's spec.

**Ingreso** — the only unauthenticated entry point (§B.8.4·5). No shell. A 380px L2 card centred on `#fbfbfb` at `--radius-panel`, `padding:32px`, `--shadow-plane`: the 24px brand square, the wordmark `Botica` at `t-14`/500, a `t-20` heading `Iniciar sesión`, a 34px email field, a 40px full-width primary `Continuar`, and an 11px `#727272` line — `El acceso a Botica es por invitación. Pida el enlace a la administradora de su droguería.` There is no sign-up link and no password reset that creates an account.
- *Loading*: the submit button takes its busy form — `Entrando…`, `aria-busy`, opacity unchanged (§B.6.2). Nothing else.
- *Error*: four distinct messages, never one. Unknown address — `No encontramos una cuenta con ese correo.` Suspended person — `Su cuenta está suspendida. Pida a la administradora de su droguería que la reactive.` Suspended network — `Esta droguería está suspendida. Escriba a soporte para reactivarla.` Unreachable backend — region scope, `No pudimos conectar con el servidor.` with `Reintentar` (§B.10.3). A generic `Credenciales inválidas` tells an attacker nothing and tells a cashier nothing either.
- *Empty, denied*: not applicable.

**Aceptar invitación** — consumes a token and lands the new person in their network at the invited role, on their invited sede. Same card geometry. The token is read from `location.hash`, the hash is cleared, and `POST /api/invitations/preview` renders the droguería's name, the address and the role before anything is typed.
- *Loading*: a skeleton of the real field stack (§B.10.1).
- *Error*: three route-scope messages, each with the same next step. `Esta invitación ya fue usada.` · `Esta invitación venció el 12/09.` · `No reconocemos esta invitación.` — each followed by `Pida un enlace nuevo a la administradora de su droguería.` and a `user-select:all` correlation id at `t-10` mono (§B.10.3).
- *Empty, denied*: not applicable.

**The application shell** — §A.13 and §B.8, the frame every later stage renders into.
- *Sidebar*: 280px L0, organisation header at 64px carrying the brand square and `tenants.name`, the collapse control to the 64px icon rail (§B.8.1); the flat seven-item nav in the drawn order — **Panel · Inventario · Compras · Precios · Mostrador · Sedes · Reportes** — at 38px per item with its counter slot; the version stamp `Botica 2.4.1` at the foot of the nav block in Geist Mono 10px/0.18em/uppercase `#909090`; the user footer at 64px with the name at 12px and the role and sede at 11px, reproducing `Marcela Ríos · Administradora` and `Andrés Peña · Mostrador · Chapinero`. **The organisation name is a label, not a control** — there is no workspace switcher in v1 and an affordance promising one is worse than none (§B.8.1).
- *Counters*: **every counter renders nothing at S0**, because zero renders nothing at all — not a `0`, not a dot, not a dimmed badge (§B.8.2). The slot, the placement rule, the `#727272`/ink-on-active behaviour and the single critical-colour exception ship here; S4 fills `Mostrador` and S6 fills `Compras`. `/api/nav-counters` polls at 30 seconds on office surfaces — a queue count is not urgent, and a 10-second poll on seven counters is seven times the traffic for a number nobody watches change.
- *Header*: 64px sticky, `z-30`, `padding:0 40px`, breadcrumb at 12px `#727272` with a `#c8c8c8` `/`, exactly one `t-28` title per route, actions right at `gap:8px` (§A.13.2, §B.8.5).
- *Version*: the sidebar stamp measures 2.90:1 and is the one informational value below AA in the system, accepted **only** because the settings dialog states the same version at full contrast (§B.15). S0 ships both. Removing either one means the stamp steps to `#6b6b6b`.
- *Loading*: the chrome paints immediately — brand square, organisation header, version stamp, the sidebar's own plane — and the content region shows a skeleton reproducing the geometry it replaces (§B.10.1). **The nav list renders as skeleton items until `/api/me` resolves the role, never as the seven-item administrator nav that then collapses to two.** A cashier watching five items disappear on every sign-in learns that the application is unsure what they may do, and the flash is also a two-frame advertisement of routes they will be refused. The user footer's name and role are skeletons on the same rule.
- *Panels and overlays*: the 440px record panel **pushes** the content region and takes no scrim, so the table behind it stays navigable — which is the whole point of `j`/`k` (§B.8.5). A modal is scrimmed at `rgba(0,0,0,0.32)`, focus-trapped, and restores focus to its trigger. A toast is bottom-right for five seconds and **never carries the only copy of an action** (§B.10.3).
- *Empty*: every route S0 ships renders a titled empty state until its owning stage fills it.
- *Error*: route scope — the empty-state geometry, a retry, and a selectable correlation id (§B.10.3).
- *Denied*: **an item a role cannot reach is not rendered, and is never rendered disabled** (§B.8.3). A `cashier` sees **Mostrador** and a read-only **Inventario**, and nothing else — the prototype's Mostrador screen draws a cashier the full seven-item nav and **that is a prototype artefact that must not be reproduced**. A direct URL to a route the role cannot have refuses **inside the content region**, naming the role it needs, and does not redirect silently: a link that shows nothing is indistinguishable from a broken one.

**The seven empty routes** — `/dashboard` (Panel, S9), `/inventory` (Inventario, S3), `/purchasing` (Compras, S6), `/pricing` (Precios, S7), `/counter` (Mostrador, S4), `/locations` (Sedes, S3 · S9), `/reports` (Reportes, S9). `/` redirects by role: Panel for `owner` and `admin`, Mostrador for `cashier` (§B.8.3). Each route renders the **deliberately-empty** kind of §B.10.2 — a title naming what will live there and a body naming what has to happen first, with no action, because S0 owns none of the actions that fill them.

| Route | Nav label · title | Empty body | Filled by |
|---|---|---|---|
| `/dashboard` | **Panel** · `Resumen de red` | `El panel se arma con la venta de las sedes. Aparece cuando el mostrador registre las primeras ventas.` | S9 |
| `/inventory` | **Inventario** · `Existencias` | `Las existencias aparecen cuando se cargue el catálogo y se reciba la primera mercancía.` | S3 |
| `/purchasing` | **Compras** · `Órdenes` | `Las órdenes sugeridas aparecen cuando el modelo tenga historia de venta para aprender.` | S6 |
| `/pricing` | **Precios** · `Propuestas` | `Las propuestas de precio aparecen cuando haya venta suficiente para estimar la elasticidad.` | S7 |
| `/counter` | **Mostrador** · `Venta` | `El mostrador se habilita cuando la sede tenga catálogo y existencias.` | S4 |
| `/locations` | **Sedes** · `Red` | `Las sedes se crean desde la administración de la plataforma. Escriba a soporte para agregar una.` | S3 · S9 |
| `/reports` | **Reportes** · `Reportes` | `Los reportes se calculan sobre las métricas diarias de la red. Aparecen con la primera venta.` | S9 |

**S1 replaces Inventario's body with the never-populated kind and its `Nuevo producto` primary** — S1 departs from §B.8.4·7's `Cargar catálogo` deliberately, because no tenant-facing import wizard exists in v1 and a primary button that opens nothing is worse than one that creates a product (S1, *Gated on*) — a first-run Panel drawn with six zero-value KPI tiles is indistinguishable from a broken one. S0 cannot offer an action that does not exist, and a button that does nothing is worse than a sentence that is true.

**Ajustes** — one dialog, not routes (§B.8.4·4). 1120 × 720 capped at the viewport, L3, centred over the `rgba(0,0,0,0.32)` scrim, opened by the sidebar's gear and by `⌘,`. The open section is a search param on whatever route is showing — `/inventory?settings=people` — so a section is a link, the dialog never takes the page out from under anyone, and `Escape` returns exactly where you were. **S0 renders three rail items and no more**: **Organización → General, Personas** and **Registros → Actividad**. A section a later stage owns is not in the rail at all; §B.10.2's rule is that a section a capability can empty is gated at its header, not inside its body.

**Ajustes · General** — the network's identity, and the one `settings` key group S0 writes.
- Editable: `Nombre de la droguería`, `NIT`, `Razón social`, `Zona horaria`. Read-only, with the reason stated once at `t-12` `#727272`: `Botica opera en Colombia. La moneda y el formato de números no son configurables en esta versión.` The `slug` is set at provisioning and is not editable — a slug that changes breaks every link anyone saved. The running version is stated here at full contrast.
- Form controls and the shared ring (§B.5), `[Cancelar][Guardar]` right-aligned at `gap:8px` with one primary (§B.6.2), validation **on blur and on submit, never on keystroke**, and the error replacing the help text in the same slot so validating shifts no layout (§B.5.7).
- *Loading*: skeleton of the real field stack. *Error*: field scope for validation, region scope for a rejected save. *Denied*: a `cashier` does not reach the dialog; an `admin` edits identity and not billing or API-key settings (§2). *Empty*: not applicable.

**Ajustes · Personas** — the roster and the outstanding invitations, in one list.
- Standard density (§B.4.1). Columns: `Persona` · `Perfil` · `Sede` · `Estado` · `Último ingreso`. Status is a **dot plus label with no pill**, because this is a status shown incidentally rather than the column the surface is about (§B.7.3). An invitation renders in the same list at **Pendiente** (neutral, hollow), **Vencida** (warning, hollow), **Revocada** (neutral, solid) or **Envío fallido** (critical, solid) — a roster that hides the people who were invited and never arrived is a roster nobody trusts.
- Actions: `Invitar` as the section's one primary; per row, `Reenviar`, `Copiar enlace`, `Revocar` for an invitation, and the role select, the sede select and `Eliminar` for a person. Delete sits behind a confirmation whose body names the consequence and whose confirm button carries it — `Eliminar a Andrés Peña`, the one place a destructive button is filled (§B.6.2).
- *Loading*: skeleton rows at the real 48px height and the real column widths (§B.10.1). *Empty*: never-populated kind — a title naming what inviting does and the `Invitar` primary. *Error*: row scope — the failing row keeps its place with the critical badge and the reason beside it; **the row does not turn red** (§B.10.3). *Denied*: a `cashier` does not reach the dialog. An `admin` sees the roster; the role select is absent on rows they may not change, `Eliminar` is absent entirely, and `Invitar` opens with the role fixed at **Mostrador** — absent, not disabled (§B.8.3).

**Ajustes · Actividad** — the append-only trail (§3, §9). **Compact density**, 40px rows, which §B.4.1 names for exactly this surface.
- Columns: `Cuándo` · `Quién` · `Acción` · `Entidad`. Timestamps take the tabular treatment and the relative-time ladder of §B.9.1 up to 12 hours, then the absolute stamp. A row expands **in place** to show `before` and `after`; it does not open the shell's 440px record panel, because a panel that pushes a dialog has nowhere to push to.
- **This is the grid contract's first real consumer**, server-paginated with `rowCount` from the API. Its page, size, sort and filter state is **component-local rather than in search params** — §B.8.4·4's deliberate departure, and the primitive takes its state through an adapter so a route and a dialog get the same server contract. *If the dialog wrote search params*, `Escape` would have to restore the underlying route's own params and would lose the caller's filters.
- *Loading*: skeleton rows. *Empty*: deliberately-empty kind, no action — there is nothing for a reader to do about an empty history. *Error*: route scope inside the pane. *Denied*: `cashier`. **No control on this surface writes, updates or deletes**, and the database grant makes that structural rather than editorial.

**Django admin** — the platform-admin surface (§2, §9). It is outside the design system entirely and gets no styling budget (§B.8.4·7). Two requirements: nothing in it is ever shown to a tenant user, and a tenant-scoped model list does not render until a tenant is selected.

## Offline

**Nothing in S0 is offline-capable, and that is the honest answer** — the local store is S2's (A4) and until it exists there is no offline data layer to read. What S0 owes the chain is the shell that will *survive* a blackout once there is something behind it, and the two rules that keep the offline contract from being poisoned before it is built.

**The application shell is precached and paints with no network.** The service worker (Workbox, §9) precaches the built HTML, JS and CSS, the two self-hosted Geist faces and the icon set. A reload with no connection paints the sidebar, the header and the route chrome rather than the browser's offline page. **The fonts are self-hosted, not fetched from Google Fonts at runtime**: a shell that paints in a fallback face when the link is dead is a shell whose fixed-percentage table columns drift, and every width in §A.17 is measured in Geist.

**The service worker never caches an API response.** Not `/api/me`, not `/api/locations`, not a settings read, not one byte under `/api/` (§9). The local store is the offline data layer, and two caches are two truths — the specific failure being a stock level served from an HTTP cache to a screen a cashier is about to sell from. This is a rule about the whole product, enforced in the only place it can be enforced, and it is set at S0 because by S2 it is too late to find out that it was not.

**A new shell version activates on the next full load, never mid-session.** Swapping the running application under an unsent sale is precisely what S2's queue and S4's ticket cannot tolerate, so the rule is fixed here rather than discovered there. A pending update is a line in the settings dialog, not a banner and never a forced reload.

**Every surface S0 ships is online-only, and each says so in its own words rather than failing.** Ingreso and Aceptar invitación render the region-scope error with `Reintentar`; no credential and no acceptance is ever queued for later, because a sign-in that "will happen when the network returns" is a lie about a security boundary. Inside the shell, a route whose data cannot be fetched renders §B.10.3's route scope — the operation, the reason and a retry — over a shell that has already painted, which is the difference between "the app is down" and "this screen needs the network". The settings dialog behaves identically. Django admin is never cached and never available offline.

**No API response is mirrored into browser storage anywhere in this stage.** Not the identity, not the location list, not the settings group — no `localStorage`, no `sessionStorage`, no IndexedDB. The session cookie carries the session and the server answers `/api/me` on every load. This looks like a missed optimisation and is not: S2's local store is the one place tenant data is allowed to live in a browser, under a schema, a checkpoint and a conflict handler, and a convenience cache written at S0 would still be there — unversioned, unscoped and unsynced — the day a till goes offline holding a stale sede name and an expired role. One offline data layer, and S0 does not build a second one first.

**The filter bar's right-hand provenance slot exists and is empty.** §A.13.3 draws `Sincronizado hace 4 s` there and §B.9.1 fixes the component; S2 owns it. S0 ships the slot so that adding sync state later is a component and not a layout change, and renders nothing in it — a hardcoded `Sincronizado` on a build with no sync is the worst string in the product.

## Acceptance

Each of these passes or fails while someone is watching.

1. `docker compose up` brings `web`, `worker`, `postgres` and `caddy` to healthy; Caddy serves the application over TLS; the worker connects to the Postgres queue and idles. `web` and `worker` are demonstrably the same image started with different commands.
2. Every table carrying `tenant_id` reports **both** row security enabled and row security forced. A table missing either fails. `tenants` reports the same, under its `id`-based policy.
3. The runtime database role owns **no** table in the schema, including Procrastinate's; the migration role owns them all and is a different role.
4. A `SELECT` with no `WHERE` clause, issued as the runtime role inside a transaction pinned to network A, returns only A's rows. The same statement as the migration role returns rows from both networks.
5. A code path that reaches the database outside a pinned transaction returns **zero rows**, not another network's rows. Demonstrated on a management command run without its tenant argument, which refuses to start.
6. A background job carrying `tenant_id` in its payload pins and writes correctly; the same job with the key removed refuses rather than writing unpinned.
7. A sync-push batch containing one row that names another tenant is **rejected in full**. The rows that were legitimate are not applied.
8. An `owner` invites an address at role `cashier` with a sede, the invitee opens the link, sets a password, and lands signed in on Mostrador at the invited role with the invited home sede. Attempting to reach any registration path finds nothing to register with.
9. Opening the same invitation link a second time is refused with `Esta invitación ya fue usada.` and a next step — not `Something went wrong`.
10. The invitation token appears in **no** access-log line: not the web server's, not Django's, not for the HTML request that loads the accept screen. Pasting a `/accept/{token}`-shaped URL reaches a screen that neither previews nor accepts it.
11. An invitation whose email delivery has failed five times still works: the owner presses `Copiar enlace`, sends it by another channel, and the invitee accepts.
12. An `owner` changes a person from `cashier` to `admin` and the change appears in Actividad with the actor, the entity and both before and after, without leaving the application. An `admin` signed into the same network sees no role control and no `Eliminar`, and a direct API call attempting either is refused.
13. A `cashier` signs in: the sidebar shows **Mostrador** and **Inventario** only, `/` lands on Mostrador, and pasting `/pricing` refuses inside the content region naming the role required — it does not redirect.
14. A `cashier` whose `location_id` has been nulled by a direct database write gets an error naming the misconfiguration on their next request. They do not get every sede.
15. Actividad is read-only in the strongest sense: `UPDATE` and `DELETE` on `audit_log` as the runtime role are refused **by Postgres**, and an `owner` reading it sees only their own network's rows.
16. Every elevated-role mutation this stage exposes lands an `audit_log` row with actor, entity and before/after — invitation issue, resend, revoke, role change, status change, sede change, user delete and the tenant-settings `PATCH`. A mutating endpoint that writes no row fails.
17. An `owner` edits the network's razón social and timezone through `PATCH /api/settings/tenant` and reads them back. **No tenant id appears in the path.** A second stage's key group, seeded by hand into `tenants.settings`, is still there afterwards — the write did not read-modify-write the column (ledger rule 5).
18. Two networks exist on one instance and a person in each sees zero rows belonging to the other across every table this stage created.
19. A `platform_admin` reaches Django admin, selects one network, sees its rows, and can produce no view of both at once. With no selection, every tenant-scoped list is empty rather than global.
20. The frontend client is generated from `/api/openapi.json`: removing a field from an endpoint surfaces as a type error at the call site, not at runtime.
21. **Design-system conformance** (§B.16), checked against the built shell: every font size is one of the eight steps of §B.1; every radius is 4, 6, 7, 9, 12, 16 or 999; every interactive element renders `2px solid #0071e3` at `outline-offset:2px` on `:focus-visible` and there is no second ring; no surface renders a spinner outside a button already pressed; no empty region reads `Sin datos`; every transition is 120–160ms `ease-out` with its properties enumerated and nothing translates; **no `dark:` variant exists anywhere in the repository**.
22. Every figure on every S0 surface goes through the §A.11 formatter: thousands dot, decimal comma, `$` unspaced with no decimals, and a non-breaking space before `M` and before a unit. No call site calls `toLocaleString`.
23. Personas and Actividad both answer `j`, `k`, `Enter`, `x`, `Esc` and `/` (§B.13.2). `⌘,` opens the dialog. **`⌘K` does nothing** and is bound to nothing.
24. With the network disabled after a first successful load, a browser reload paints the full shell — sidebar, header, chrome, in Geist — and the content region shows a route-scope error naming the operation and offering a retry. The browser's own offline page never appears.
25. The service worker's cache contains **no** entry whose path begins `/api/`, and `localStorage`, `sessionStorage` and IndexedDB are **empty** after a full session as each of the four roles. Inspected directly, not asserted.
26. Signing in as a `cashier` on a cold load never paints more than two nav items. Recorded at 60 fps and stepped through, there is no frame showing seven.
27. **Shell cold start, warm service-worker cache, to painted chrome: under 800 ms p95 on the pilot's hardware.** This is derived from §4's "counter app cold start, warm cache, to sellable < 2.5 s" — the shell is inside that budget, and S4 needs the rest of it for the local store's first read. *If S0 spends two of the 2,5 seconds*, S4 can only meet its number by not rendering the shell on the till, which the design forbids. The other §4 budgets — 30 ms to a filtered product list, 50 ms scan-to-line, 200 ms to a closed ticket, 400 ms on the inventory grid, 20 ms on a delta pull — belong to surfaces S0 does not ship; the grid primitive's obligation here is that one user interaction produces exactly one request.

## Hands off

- **Four services from one compose file** — `web`, `worker`, `postgres`, `caddy` — with `web` and `worker` on one image (§9). No Redis, no pgbouncer.
- **Two Postgres roles**: a migration role owning every table and a runtime role owning none, so no policy is bypassed by ownership (A1).
- **The table convention** every later migration follows without restating it: `id`, `tenant_id`, `created_at`, `updated_at`, the tenant policy, and `FORCE ROW LEVEL SECURITY` (§3, A1).
- **The pinning path for all five contexts of ledger rule 6**, plus the single registry-shaped unpinned resolution that returns a tenant id and nothing else. **No later stage issues its own `SET LOCAL`.** S2 registers the device-key lookup; S5 registers the provider-reference lookup; both call the same module.
- **The sync push's transaction semantics**, fixed before the payload exists: one pinned transaction per batch, and a row naming another tenant rejects the batch rather than being filtered from it (ledger rule 6).
- **The location-scoping predicate** in its two modes, raising rather than defaulting on a cashier with no home sede (A2, ledger). Never re-implemented; a stage writing its own `location_id IN (…)` is a defect.
- **`tenants`** with `name`, `slug`, `nit`, `status` and a `settings` JSONB carrying the `tenant` group; the seven other groups are empty and belong to the stages the register names, all written through the single `jsonb_set` helper that raises on a zero-row update (ledger rule 5).
- **`locations`** — the **sede** — with `code`, `name`, `type` admitting `distribution_center` from day one (A10), `address`, `city`, `phone`, `status`, and a Django-admin CRUD. S10 adds the tenant-facing provisioning flow; S3 and S9 fill the `/locations` route.
- **`users`** with `email`, `name`, `role`, nullable `tenant_id`, nullable `location_id`, `status`, `last_login_at` and the `platform_admin` flag, under the constraint that a `cashier` has a home sede.
- **`invitations`** and an invite-only creation path. **No self-signup exists.** The token is hashed at rest and travels in a body or a fragment, never in a path; the access-log formatter scrubs the retired path shape.
- **The four-role enum and the one permission dependency** every endpoint calls, which is also where `tenants.status` and a person's own `status` are enforced (§2).
- **`audit_log`**, the write path, the `owner`/`admin` reader, and a runtime role that holds no `UPDATE` or `DELETE` on it. Every later stage with an elevated-role mutation appends through that path; none re-exposes the reader (ledger).
- **A referential rule for `users`**, because `owner` may hard delete: every stage referencing `users` does so `ON DELETE SET NULL` and stamps the human-readable identity it needs at write time — `audit_log.actor_email` is S0's instance of the pattern. *If a stage takes a hard FK instead*, deleting a person who once closed a turno either fails or erases the turno's attribution, and both are discovered at an audit.
- **Django admin** as the platform-admin surface, ready to host S1's load tool and S10's provisioning, requiring a tenant selection before any tenant-scoped list renders.
- **Procrastinate's schema and a running worker**, with the tenant-pinned job wrapper proven by S0's own job. Chain-wide infrastructure — inherited by every stage that queues work, not by S1 alone.
- **The transactional email adapter** (§10), whose failure never blocks the operation that triggered it.
- **The django-ninja API, `/api/openapi.json` and the typed client**, plus the two shape rules: the English `/api/` prefix everywhere, and a settings group as a `GET`/`PATCH /api/settings/{group}` pair on the pinned tenant with no tenant id in the path.
- **`/api/me` and `/api/nav-counters`**, the two endpoints the shell runs on. A stage adds its counter key to the second rather than fetching its own.
- **The design-system Part B component layer in full**, including the counter-density tokens and props that S4 will select and the four chart forms S9 will use.
- **The single number and date formatter** (§A.11), the only place `Intl` is touched.
- **The application shell** — sidebar with its seven role-gated items and their counter slots, header, filter-bar slot, content region, record-panel slot, settings dialog with its search-param section — and one empty route per later-stage surface for its owner to fill.
- **The grid contract as a primitive** with a state adapter: typed search params on a route, component-local inside the dialog, one server contract either way.
- **The PWA shell and its service worker**, precaching the shell and the self-hosted fonts, caching nothing under `/api/`, and activating a new version only on a full load.

## Gated on

**Nothing.** None of architecture.md §11's six open questions blocks this stage — §11.1 is a per-client mapping rather than a blocker, §11.2 is a quality variable and blocks nothing (§1, *Cold start*), §11.3 blocks demonstrating S8 on real customers, §11.4 blocks S7, §11.5 blocks validating S2's and S4's budgets on real hardware, and §11.6 is a rollout sequence rather than a build dependency. S0 can start today.

Three things are recorded rather than gated, because this stage decided them rather than deferring them.

**Names coined here**, because neither architecture.md §3 nor the ledger supplies them: every column of `invitations`; `audit_log.actor_email` and `audit_log.request_id`; the four status value sets and the decision to carry them as checked text rather than as enum types; the `tenant` settings keys `legal_name`, `timezone`, `currency` and `number_format`; and the path `/api/nav-counters`. All are English, in the register of the names around them, and any of them can be renamed by one migration before S1 lands.

**Decisions taken that architecture.md does not settle**, listed together so a reviewer can find them: who may issue an invitation and at what role; what `app.tenant_id` holds for a `platform_admin` before a selection (nothing, and zero rows follow); that identity comes from the session while authorisation is re-read inside the pin; the four Spanish role labels; that an expired invitation is derived rather than stored; and the 800 ms shell-paint budget derived from §4. Each carries its consequence where it is stated.
