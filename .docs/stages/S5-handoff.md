---
stage: S5
title: Handoff
depends_on: [S4]
blocks: [S9]
source: "architecture.md §0, §2, §3, §4, §5, §8, §9, §10, §11.1, §11.5, §11.6, §12, §13; amendments A1, A2, A5, A6, A7, A9; ownership.md rules 1–9, the disputed-column table, the settings register and the cross-stage service table; design-system.md §A.11, §A.16, §B.4, §B.7, §B.8, §B.9, §B.10, §B.11, §B.12, §B.13, §B.17; ../handoff/README.md"
---

# S5 — Handoff

## Outcome

**Botica does not issue fiscal documents.** It does not allocate a fiscal number, does not generate a CUDE, does not sign an XML, does not speak the DIAN's protocol and does not contract a proveedor tecnológico (§8, A9, §12). Every droguería trading today already meets that obligation with some system, and that system keeps meeting it. This stage's entire responsibility is to build **one canonical sale document** and hand it to whatever system the client already runs, over that system's API, **exactly once**. That is one deliverable, not a compliance programme.

Two failures define the work, and everything below is shaped by them. **An incomplete payload is the one that kills a pilot**: a field the receiving system needs and we did not send is a field a cashier re-types, and a handoff that gets re-typed is worse than no handoff, because it costs the counter time and buys nothing. **A duplicate is the one that costs money**: a second fiscal document at the far end is signed, numbered and filed with the DIAN under the pharmacy's own resolution, and unwinding it means issuing a credit note against a sale that never happened. A missing document is a retry; a duplicated one is a tax problem. That asymmetry is why the delivery is built to be safely re-runnable from the first line of code rather than made idempotent later.

**Nothing about how a cashier sells changes, and nothing about the handoff is visible at a counter.** They scan, they press `Cobrar`, and the receipt carries `sales.number` — the internal, locally allocated, always-present number that is the key both systems share (§8). The document is built server-side from the sale the till pushed, delivered by a job, and moves `pending` → `sent` → `acknowledged`, with `failed` for a delivery the target refused. Those four states describe **our handoff**, not the receiving system's filing with the DIAN; Botica does not know and does not claim to know what the DIAN did (ledger). The §4 budget of **200 ms p95 from `Cobrar` to the closed ticket, offline included**, is untouched by this stage because nothing on that path became fiscal.

**The default is off, and off is not broken.** With no target configured — a demo, a pilot's first week, a client who has not yet given us their API — there are no `fiscal_documents` rows, no queue, no retry job, no work list, no Panel figure and no error, warning or badge anywhere (§8). The only place the state is visible is the settings screen that would turn it on. An administrator whose target *is* configured gets one screen and one number: the work list of deliveries that have not landed, with the reason for each in words, and the count of what is unsent with the age of the oldest. Failures land there and never on a cashier's screen — a cashier cannot fix a malformed acquirer NIT with a customer waiting, and a red badge in front of a stranger produces a conversation neither of them can finish.

## Inherits

**From S0** — the four-service stack and the migration/runtime role split; the table convention (`id`, `tenant_id`, `created_at`, `updated_at`, an RLS policy, `FORCE ROW LEVEL SECURITY`) this document does not restate (§3, A1); **tenant pinning** (rule 6), of which this stage uses three contexts — the HTTP request, the background job, and the sync push whose already-open pinned transaction is where a document row is written. **S5 uses no unauthenticated inbound path at v1**, which is a deliberate reduction: the earlier draft needed one for a provider callback, and a target that reports asynchronously is now polled through its mapping's query operation instead. The four-role enum and the single permission dependency (§2); the audit write path every mutation here appends through (ledger); Procrastinate's queue and a running worker (§9); the transactional email path used for the failed-delivery digest (§10); `tenants.settings` **the column** and the rule-5 helper — S5 inherits the column and owns exactly one key group in it; the instance's per-tenant secrets store, where the target's credential lives and where a JSONB column never does; Django admin, which is where a raw target response is read; the design-system Part B component layer, the shell and the grid contract (§9, design-system.md Part B).

**From S1** — `customers` (`document_type`, `document`, `name`), read to identify the acquirer and never written here; `items` (`name`, `presentation`, `strength`, `unit`, `vat_class`, `tracks_stock`) and `item_barcodes` (`code`, `is_primary`), which supply the line's description and its code; `tenants.nit` and `tenants.name`, the emitter's identity on every document; `locations` (`code`, `name`, `address`, `city`), the sede the sale happened at.

**From S2** — the pull/push protocol and its pinned apply path (rule 6, fourth context), inside which a document row is written. Nothing else. **S5 requests no sync-registry amendment (rule 9) and puts no table on a till**, which is the second reduction the re-scope buys: the earlier draft synced leases, resolutions and the shift's documents to every device, and with numbering gone the counter carries none of it and the delta pull is untouched. `devices` is neither read nor written by this stage at all, and the ledger names S2 alone as its writer.

**From S3** — nothing directly. S5 moves no stock and writes no `stock_moves` (rule 7). A credit note is a document; the units came back through S4's `customer_return` move.

**From S4** — `sales` (`location_id`, `number`, `status`, `source`, `customer_id`, `subtotal`, `discount`, `tax`, `total`, `sold_by_user_id`, `device_id`, `occurred_at`, `recorded_at`), `sale_lines` (`position`, `item_id`, `quantity`, `unit_price`, `discount`, `vat_class`, `tax_amount`), `payments` (`method`, `amount`, `reference`), `sale_returns` and `sale_return_lines`. All read; none written. **`sales.number` is the internal per-location sale number, composed as `{device code}-{per-device sequence}`, and it is not a fiscal number** — nothing in this stage allocates one, and whatever number the receiving system issues arrives back as `fiscal_documents.external_number` (ledger, disputed columns). Also inherited: the guarantee S4 states in its own *Hands off* — that a closed sale is complete and immutable at commit, with `unit_price`, `discount`, `vat_class` and `tax_amount` stamped per line — which is what lets this stage build a document without re-deriving a peso from a price list; the acquirer attach in the cobro dialog, which works offline; and the void path, whose handoff consequence is S5's.

## Scope

### In

1. **`fiscal_documents`** and the **`fiscal_document_status`** enum (`pending`, `sent`, `acknowledged`, `failed`), with its state machine, its attempt trail and its `document_key` uniqueness (§3, ledger).
2. **The canonical sale document** — one complete representation of a closed sale, and of a return as a credit note referencing it. A pure function of the sale, built when it is about to be sent. This is the stage's real deliverable.
3. **The sale handoff service** — the ledger's sixth cross-stage service, owned here and called by S4's sale-close and return paths inside their already-pinned transaction. The only writer of `fiscal_documents`.
4. **Exactly-once delivery** — the derived `document_key`, the delivery job, the backoff ladder, the queueing lock, and the rule that an ambiguous timeout is resolved by a query and never by a blind resend.
5. **The mapping layer and the target interface** — `deliver`, `query`, and optionally `fetch_representation`. The canonical payload is ours; the field names are theirs. Adding a target is a mapping plus credentials.
6. **The file export target** — the same payload, on a schedule, for a client whose system has no API.
7. **Unconfigured behaviour** — specified below as behaviour a build agent implements and a reviewer checks, not as a principle.
8. **The failed-delivery work list** — `Envíos a facturación`, an office route holding what has not landed, what the target refused, and the sales that hold no document at all.
9. **The summary endpoint**, which reports *not configured* distinctly from *zero* and which S9's Panel reads.
10. **The `invoicing` settings group** — `/api/settings/invoicing`: the target and its environment, the mapping in force, per-sale or batched delivery, the retry policy and the digest recipients (ledger).
11. **The sale's fiscal read-out** — whatever the target returned, on the sale detail and on a reprint, rendered as nothing at all when it returned nothing.

### Out

- **`dian_resolutions`, `numbering_leases`, and any allocation of a fiscal number** — not built at v1 and in no migration (A6, ledger). The design is preserved under *Gated on* and nowhere else.
- **CUDE generation, XML, signing, DIAN annexes, a proveedor tecnológico contract, DIAN transmission of any kind** (§8, §12, A9). A proveedor tecnológico is one possible target with one more mapping, and it is not a special case anywhere in this design.
- **Any claim that Botica makes anyone compliant.** It hands over accurate data. What the receiving system does with it, and whether the customer gets the fiscal document at the counter or later, is the client's flow (§8).
- The sale, the ticket, the payment split, the change calculation, the turno, the return's stock movement and the void itself — **S4**.
- Stock. No `stock_moves` row is written here (rule 7).
- The Panel and its tiles — **S9**. S5 owns the figure; S9 renders it, and renders nothing when there is nothing to render.
- `daily_metrics` — **S9**. Nothing here writes a rollup.
- The compliance vault — **S10**. What a target returns hangs off `fiscal_documents` and is explicitly not `compliance_documents` (ledger).
- Object-storage provisioning and lifecycle — a deploy concern (§10). S5 writes files and expects the bucket to exist.
- **`sales.source = imported` history** — **S6**. An imported sale was never issued by anyone through us and must never be handed to a target (ledger, disputed columns). The service refuses one.
- **An unauthenticated inbound endpoint.** No target callback ships at v1; a target that requires a webhook brings rule 6's fifth context with its own mapping.
- **The sync state `blocked`.** Nothing in this stage produces it; with numbering deferred it has no producer anywhere in the product (§5, A6).
- Thermal printing, cash-drawer control and any local agent (§12).

## The canonical sale document

**This payload is the deliverable.** Everything else in the stage is plumbing around it, and its completeness is the difference between a working handoff and a cashier keying every sale twice.

**Two of its groups are not a design choice.** Resolución DIAN 000165 de 2023 and its Anexo Técnico DEE 1.0 require that the acquirer be identifiable by name and identification number, and that tax be stated per line rather than per ticket — which is also why `items.vat_class` exists per item and not per tenant (§3). That is the whole of the regulation's presence in this document: it explains why the payload has the fields it has. The obligation itself is the pharmacy's and is met by the system it already runs.

### The payload

One JSON object per document. Amounts are integers in COP unless a mapping declares otherwise; timestamps are ISO 8601 with an offset.

| Group | Fields | Why it is there |
|---|---|---|
| `document` | `document_key`, `type` (`sale` \| `credit_note`), `sale_number` (`sales.number`), `references` (for a credit note: the original's `sale_number` and `document_key`), `occurred_at`, `recorded_at`, `currency`, `device_code`, `sold_by` | `sale_number` is the key both systems share (§8) — without it, reconciling a day of sales against a day of invoices is manual and gets done at month end by someone who did not make the sale. Both clocks travel because they answer different questions (§5 rule 4) |
| `emitter` | `nit`, `name`, `location` (`code`, `name`, `address`, `city`) | The receiving system issues under the pharmacy's own resolution, and that resolution is per sede (§2). A target that has to guess which sede a sale came from issues it against the wrong range |
| `acquirer` | `is_final_consumer`, `document_type`, `document`, `name` | The document must identify the acquirer. `is_final_consumer` is `true` with the other three absent for the majority case, and the mapping supplies whatever generic identifier that target expects — the exact form belongs to the target, not to this payload |
| `lines[]` | `position`, `item_code`, `item_id`, `description`, `quantity`, `unit`, `unit_price`, `discount`, `vat_class`, `tax_rate`, `tax_amount`, `line_total` | Tax is per line because most medicines are excluded from IVA and a large share of what a droguería actually sells is not (§3). `item_code` is the item's primary barcode — what the pharmacy and its accountant already recognise — and `item_id` travels beside it because a uuid is what makes a mapping reproducible when a barcode is re-used or missing |
| `totals` | `subtotal`, `discount`, `tax`, `tax_by_class[]` (`vat_class`, `taxable_base`, `tax_amount`), `total` | Copied from `sales`, never recomputed. `tax_by_class` exists because targets ask for the base and the tax per rate and deriving it at the far end is how two systems come to disagree about a peso |
| `payments[]` | `method`, `amount`, `reference` | The methods are S4's enum verbatim; a target's own vocabulary is the mapping's problem, not the payload's |

**A service line is not a special case.** An item with `tracks_stock = false` renders exactly like a product — code, description, quantity, price, `vat_class`, tax (A7). That is the whole cost of supporting toma de presión and inyectología on a fiscal document, and it is why they are the same table.

**Totals are copied and lines must reconcile to them.** The builder asserts that the lines sum to `subtotal`, `discount` and `tax` to the peso, and that the payments sum to `total`. A payload that fails this assertion is **never sent** — it is written `failed` with the arithmetic named. Sending a document whose lines contradict its total means the target either refuses it or, worse, accepts it and files a figure the pharmacy's own books do not have.

### What a return carries

A `sale_returns` row becomes a **credit note**: `type = credit_note`, its own `document_key`, `references` naming the original sale's `sales.number` and `document_key`, and **only the returned lines, at the quantities and the prices originally charged** — read from `sale_return_lines`, never re-derived from a price list, because the price may have changed since and the document must describe the money that actually moved (§5, S4's *Hands off*). Amounts are positive; the sign is carried by the type, so that no target's mapping has to guess whether a negative total means a credit or a data error. Its `occurred_at` is the return's own, not the sale's.

**A void is a credit note, always, and if the original has not yet been delivered the two go out in order.** The alternative — cancelling a queued document so the target never hears of that sale — was rejected: it produces a state where our record says a sale exists and the target has never seen the `sales.number` both systems reconcile on, and a sale number that reaches the target only sometimes is a reconciliation nobody can automate. What this costs is a sale and its reversal arriving at the far end seconds apart, which is bookkeeping every accountant already handles. What the alternative costs is a hole nobody handles. **If a pilot's client asks for the opposite**, the change is one branch in the service and a fifth state on the enum, and this paragraph is the argument to overturn.

### When a required field is missing

**This is the failure mode that kills a pilot, so it has a specified path and the path never reaches the counter.**

The builder validates before anything is queued: the emitter's NIT is present; the acquirer block is complete — either `is_final_consumer` or all three of type, number and name; every line has a code, a description, a positive quantity, a unit price, a `vat_class` and a tax amount; the totals reconcile; there is at least one payment. Validation is on the canonical payload, once, for every target — not inside a mapping, where each new client would rediscover the same missing NIT.

**A validation failure is not a refused sale and not an error at a counter.** The sale is closed and the customer has left (§5 rule 2). The document row is written with `status = failed` and `error` in words a person can act on — `El adquiriente no tiene número de documento`, `La línea 3 no tiene clase de IVA` — and it lands on the work list. An administrator fixes the cause where the cause lives, in the sale's customer or in the catalog, and presses `Reintentar`, which **rebuilds the payload from the sale as it now stands**. Nothing about the fiscal row is edited, no migration is written, and no correction is typed into a document.

**A field the target requires that the canonical payload does not carry is a mapping defect**, found on the first delivery to that target, and it is fixed in one of three places in this order: derived inside the mapping from what the payload already has; added to the canonical payload for everyone, on the reasoning that if one receiving system needs it a second one will; or asked of the pharmacy once, as a constant in settings. **Never captured at the counter.** A field a cashier types per sale to satisfy one client's system is how the till gets slower with every client won, and the counter's budgets (§4) are the thing this product is sold on.

### The document is derived, not stored

**The canonical document is a pure function of the sale (or the return) and the mapping version in force.** It is built when it is about to be sent. `fiscal_documents.payload` records what was actually sent on the last attempt — it is evidence, not input, and nothing reads it back to re-send.

Four things follow, and they are the reason the whole product can ship with this integration switched off:

- **An instance can run unconfigured for months and lose nothing.** No artefact is missed, because none was ever due. Connect a target later and any sale in the database can be rendered into a document and delivered on demand, and no backfill mechanism has to exist for that to be true.
- **A correction upstream is picked up by a retry.** Attach the customer, fix the NIT's check digit, correct a catalog description — the next attempt renders the current truth.
- **A mapping fix re-renders every stuck document.** A target that changed a field name is one mapping version and a bulk retry, not a data migration over rows we cannot legally edit.
- **And nothing is backfilled automatically.** Connecting a target does **not** queue a month of history. A pharmacy that has been invoicing in its own system all month does not want a month of duplicates (§8), which is precisely why nothing was queued while unconfigured. Delivering a historical range is a client's decision, and v1 ships no action for it: the day one asks, it is a date range and a confirmation over an endpoint that already exists, not a new mechanism.

## Delivery

### Exactly once

**`fiscal_documents.document_key` is derived from the sale, stable across every retry, and is the far end's dedupe key** (ledger). Its composition: `{location.code}-{sales.number}` for a sale, and `{location.code}-{sales.number}-NC{n}` for the n-th credit note against it. `sales.number` is already unique within a location and composed as `{device code}-{per-device sequence}` (S4), so the key is tenant-unique, human-readable, and — the property that matters — **reconstructible from the sale alone by any process at any time**. It contains no timestamp, no attempt counter and no random component, because anything that varies per attempt destroys the only guarantee it exists to provide.

`UNIQUE (tenant_id, document_key)` puts that guarantee in the database rather than in the job. Two concurrent calls of the handoff service for one sale produce one row and one no-op.

**Every mapping sends `document_key` as the target's idempotency key or external reference.** The one question asked of every new target, before anything else, is which field that is. **If a target has no such field, its mapping must declare a `query` operation** — and if it has neither, delivery to that target is capped at one attempt per document, and an ambiguous outcome goes straight to the work list for a human. That is a deliberately poor experience for a poorly-behaved target, and it is correct: a blind retry against a system that cannot dedupe is how a pharmacy ends up with two signed fiscal documents for one sale.

**A timeout is not a failure and is never resolved by re-sending.** After a timeout, a dropped connection, or a 5xx with no body, the outcome is unknown — the target may have committed. The next attempt **queries first** by `document_key`; if the target holds the document, the row moves to `acknowledged` with whatever the query returned, and nothing is sent. Only a query that comes back empty leads to a second delivery. This is the one place in the product where at-least-once is not good enough (§8, ledger), and the reason is asymmetric cost: a document that never arrived is found by the work list and delivered on the next attempt, while a document that arrived twice is signed, numbered and filed twice under the pharmacy's own resolution.

One attempt is in flight per document at a time, enforced by a Procrastinate queueing lock on `fiscal_document:{id}` — a forced retry pressed twice enqueues one attempt.

### The mapping layer

**A target is three things: a transport, a declarative mapping, and a credential.** The transport is HTTP with an auth scheme, or the file writer. The mapping declares the envelope, the field names, and the value vocabularies — payment method, document type, `vat_class` and its rate — plus which field carries `document_key`. The credential lives in the instance's secrets store, keyed per tenant, and never in `tenants.settings` (§9): a credential in a JSONB column is a credential every `admin` query can read.

Three operations, and no other module in the system knows a target's name, its payload shape or its error vocabulary:

| Operation | Contract |
|---|---|
| `deliver(document)` | Send one canonical document. Returns one of three outcomes: *held*, with whatever identifiers came back (→ `acknowledged`); *taken, confirmation later* (→ `sent`); or *refused*, with the target's reason parsed into one sentence (→ `failed`) |
| `query(document_key)` | Does the target hold this document, and what are its identifiers? Used after an ambiguous timeout and by the dwell re-query. Required of any target whose mapping does not declare an idempotency field |
| `fetch_representation(external_id)` | Optional. The target's own PDF or link, stored as `pdf_url` |

**Adding a target is a mapping plus credentials, and it is never a change to the sale.** That is checkable: the diff that adds a second target must touch no file under the sale, the ticket, the sync path or the canonical builder. If it does, the payload was incomplete and the fix belongs in the payload for everyone, not in a branch for one client (§8).

`fiscal_documents.mapping_version` records which mapping produced the stored `payload`, because a delivery that succeeded a year ago and fails today is otherwise indistinguishable from a target that changed its API.

### Off when unconfigured

**With no target configured, the handoff does not exist** (§8). This is the default, it is the state every demo runs in, and it is a supported configuration rather than a broken one. Specified as behaviour:

- **One predicate.** `handoff_enabled(tenant)` is true when the `invoicing` settings group names a target **and** that target's credential resolves in the secrets store. Both. It is called in exactly three places — the handoff service, the sweep job, and the summary endpoint — and a fourth place that decides for itself is how a demo instance grows a badge.
- **The service writes nothing.** Called on a sale close with the predicate false, it returns without writing a row and without enqueueing anything. S4 treats "no document" as a normal return value, not an exception (see *Hands off*).
- **No queue, no job, no work.** The sweep exits on the predicate before it runs a query. No delivery job exists to fail, no ladder ticks, no digest is sent.
- **No figure.** `GET /api/fiscal-documents/summary` answers `{"configured": false}` and no counts. S9 renders **nothing at all** — no strip, no tile, no clause appended to the freshness line. **Absent, not zero** (§8) — no strip, no clause on the freshness line, no placeholder, which is what S9 specifies.
- **No error, no warning, no badge, anywhere.** Not on the till, not on the Panel, not in the nav, not in an email, not in the audit log.
- **One place, and it is the settings screen that would turn it on.** `Ajustes · Facturación electrónica` says plainly that no invoicing system is connected and offers to connect one. The work-list route still resolves, and unconfigured it renders §B.10.2's never-populated empty state in the neutral family pointing at that section — never a count, never a warning.
- **Turning it on starts from that moment.** The settings group stamps `configured_at` when a target is first saved. Every sale closed after it is queued; nothing before it is, and the orphan check below is bounded by the same timestamp. That is the no-backfill rule expressed as data rather than as a convention someone has to remember.

**One bounded exception, because silence would be a lie.** Disconnecting a target while documents are still `pending` is a destructive confirm that names the consequence, and afterwards the settings section carries one line stating how many deliveries are held and the work list still lists them. Unconfigured-and-never-configured is silent; unconfigured-with-work-in-flight is a state a person created deliberately two clicks ago, and hiding it would hide their own decision from them.

**The general rule this is an instance of** (§8): an integration nobody has configured is off, not failing. A product that nags about unconfigured optional integrations trains its users to ignore its warnings, and the warning that then gets ignored is the one that mattered.

### The file export

**A client whose system has no API gets the same payload on a schedule.** The file target is a transport, not a second design: same canonical document, same `document_key`, same mapping, same validation, same states.

The export job renders the period's documents into one file — CSV at line grain when the mapping declares columns, JSON otherwise — writes it to object storage (§10), and moves each included document to `acknowledged`. **`acknowledged` here means the file exists**, not that anyone imported it, and the work list labels the target so nobody reads more into the badge than that. Exactly-once survives the transport: a document already written into a period's file is never written into another, and re-running an export for a period overwrites the same file with the same content rather than appending, so a re-run is a no-op at the far end.

## Data

The universal convention is S0's and is not restated (§3, A1). Every table below is created by S5 and written by S5 alone (ledger).

| Table | Change | Detail |
|---|---|---|
| — | add enum | `fiscal_document_status`: `pending` \| `sent` \| `acknowledged` \| `failed` (ledger). These describe **our handoff**, not the DIAN's filing. `accepted`, `rejected` and `contingency` are gone with the re-scope and no code path names them |
| `fiscal_documents` | create | `sale_id` nullable, `sale_return_id` nullable, `document_key`, `target`, `payload` JSONB, `status`, `attempts`, `sent_at`, `acknowledged_at`, `external_number`, `cude`, `pdf_url`, `response` JSONB, `error` (§3). `CHECK` that exactly one of `sale_id` / `sale_return_id` is non-null |
| `fiscal_documents` | add columns | **Coined, three of them, each carrying a decision.** `location_id` — denormalised from the sale so the work list and the per-sede count are one indexed read; `next_attempt_at` — nullable, and **null means held, not queued**; `mapping_version` — which mapping produced the stored payload |
| `fiscal_documents` | constraint | `UNIQUE (tenant_id, document_key)`. This is the exactly-once invariant, in the database rather than in the job. Because the key is derived from the sale, a second row for one sale is impossible by construction — which is stronger than a uniqueness rule the delivery job promises to honour |
| `fiscal_documents` | index | `(tenant_id, status, next_attempt_at)` where `status IN ('pending','sent','failed')` — serves the sweep's "what is due" query |
| `fiscal_documents` | index | `(tenant_id, location_id, status, created_at DESC)` — serves the work list's filtered, sorted page and the summary count |
| `sales` | index (rule 4) | `(tenant_id, recorded_at)` where `source = 'counter' AND status = 'closed'` — migrated onto S4's table under rule 4, serving one query: the orphan check that finds a closed counter sale with no `fiscal_documents` row after `configured_at`. Distinct from the rollup source index on `(tenant_id, location_id, recorded_at)`, which S4 creates and S9 inherits, and neither substitutes for the other |

**No `fiscal_document_type` enum is created.** A document is a credit note exactly when `sale_return_id` is non-null, and the payload's `type` is derived from that. The earlier draft coined the enum; a column that duplicates the fact its own foreign key already carries will eventually disagree with it, and a document whose type says `sale` while it hangs off a return is a defect no constraint catches.

**No acquirer snapshot columns and no `issued_at`.** The earlier draft copied the acquirer onto the row because a transmitted document must not change under an edited `customers` row. That reasoning does not survive the re-scope: nothing here is transmitted, the record of what was sent is `payload`, and re-rendering from the current sale is the *desired* behaviour on a retry (see *The document is derived, not stored*). Age is `created_at`, the universal column, because "how long has this been stuck" is a question about our handoff and not about when the sale happened.

**The state machine, in one place.** Four values, and every transition below is the only way to reach its target. Nothing else writes `status`.

| From | To | Trigger | Notes |
|---|---|---|---|
| — | `pending` | the handoff service writes the row inside the transaction that lands the sale, **when a target is configured** | delivery enqueued behind the commit. With no target, no row exists and this is not a defect |
| `pending` \| `failed` | `sent` | the target took the delivery and confirms later | `sent_at` stamped. Re-queried after the dwell |
| `pending` \| `sent` \| `failed` | `acknowledged` | the target confirmed it holds the document — in its response, on a status query, or by the export file landing | terminal success. `external_number`, `cude` and `pdf_url` land here **where the target returns them**; null is normal and is not a failure — the handoff succeeding is what `acknowledged` records (ledger) |
| `pending` \| `sent` | `failed` | the target refused the delivery, the payload failed validation, or the ladder reached its cap | the work list, never a till. `error` says what a person must do |
| `failed` | `pending` | an administrator presses `Reintentar`, or the mapping version changes | **`failed` is not terminal.** This is a handoff, not a filing, and the correction is a rebuild rather than a new document |
| `pending` | itself | a transport failure — timeout, 5xx, DNS, an expired credential | `attempts` increments, `next_attempt_at` moves along the ladder. **A transport failure is never `failed` before the cap.** Telling a pharmacy their invoicing system refused a document it never saw is worse than telling them nothing |

**`acknowledged` is a statement about the target, never about the DIAN.** Where a target returns its own number, a CUDE, a status or a PDF, they are stored and shown on the sale. Where it returns nothing, the row still records that the handoff succeeded, because the office's real question is *"is anything stuck?"* (§8). Nothing in this stage knows whether the DIAN accepted anything, and no label in it implies otherwise.

**`tenants.settings` — the `invoicing` group** (rule 5, written through S0's per-group helper, never by a read-modify-write of the column). **Empty is the default and means the handoff is off** (ledger).

| Key | Carries |
|---|---|
| `target` | the target's id, its environment (`test` \| `production`), its base URL where the target is per-installation, and `configured_at`, stamped when a target is first saved. **No credential** — the API key lives in the secrets store, keyed per tenant (§9) |
| `mapping` | which mapping and which version is in force. Changing it is what re-renders stuck documents on their next attempt |
| `delivery` | `per_sale` \| `batched`; for `batched`, the cadence and the window; for the file target, the destination prefix and the file form |
| `retry` | the ladder's cap (default **24 h**), the dwell after which a document stuck in `sent` is re-queried (default **30 min**), and the clock-skew tolerance beyond which a document is held rather than delivered (default **24 h**) |
| `notifications` | the recipients of the daily failed-delivery digest |

Reads and writes are `owner` and `admin`, except `target`, which is an API-key setting and is therefore `owner` only (§2, ledger).

## API surface

Every path carries the `/api/` prefix and is English (§3, ledger). Every endpoint runs behind the single permission dependency and inside the pinned transaction (§2, A1), and every mutation appends to `audit_log` through S0's path (ledger).

**S5 adds no endpoint a till calls**, and no unauthenticated inbound endpoint at all. The one fiscal read a counter makes is a read of its own sale.

| Method | Path | Purpose | Who can call it |
|---|---|---|---|
| GET | `/api/fiscal-documents` | The work list, server-paginated per the grid contract. Filters: status, location, target, date range | `owner`, `admin` |
| GET | `/api/fiscal-documents/{id}` | One handoff — its key, its target and mapping version, the payload as sent, the attempt trail, the target's parsed response, and its identifiers where they exist | `owner`, `admin` |
| POST | `/api/fiscal-documents/{id}/retry` | Force an attempt now, ahead of the ladder, **rebuilding the payload from the sale as it now stands**. Idempotent against the job's lock: pressing it twice enqueues one attempt | `owner`, `admin` |
| GET | `/api/fiscal-documents/summary` | `configured`, and when true: unsent count (`pending` + `sent`), the age of the oldest, and the failed count, per location and for the network. When false, the body is `{"configured": false}` and carries no counts (§8) | `owner`, `admin` |
| GET | `/api/fiscal-documents/exports` | The generated export files with their period, their document count and a signed link | `owner`, `admin` |
| GET | `/api/sales/{id}/canonical-document` | Renders the canonical payload for one sale **without sending it**. This is how a mapping gets written: the first hour of integrating a client's system is spent answering *"what exactly do you send?"*, and a screen that answers it turns a week of emails into an afternoon | `owner`, `admin` |
| GET | `/api/sales/{id}/fiscal-document` | The handoff of one sale, for the sale detail and a reprint. Returns the target's number, CUDE and PDF link where they exist. **For a `cashier` it returns those identifiers and never a status**, because a fiscal state is not a cashier's to read (§8) | `owner`, `admin`, and `cashier` for a sale at their own location |
| GET | `/api/settings/invoicing` | The group (ledger) | `owner`, `admin` |
| PATCH | `/api/settings/invoicing` | Write the group through the rule-5 helper. The `target` key is `owner` only (§2, ledger) | `owner`; `admin` excluding `target` |

## Jobs

Four jobs, all on Procrastinate, all pinning the tenant from the job payload before touching anything (rule 6), and **all of them exit immediately when the tenant has no target configured**.

| Job | Trigger | Idempotency | Failure behaviour |
|---|---|---|---|
| `deliver_fiscal_document` | enqueued behind the commit that created the row, and whenever `next_attempt_at` comes due | A queueing lock on `fiscal_document:{id}` — exactly one attempt in flight per document. Every request carries `document_key` in the mapping's idempotency field. **After an ambiguous outcome the next attempt queries first and never re-sends blind** | Increments `attempts`, stores `response` and a human-readable `error`, and schedules the next attempt on the ladder **1 min · 5 · 15 · 60 · then hourly to the policy cap**. A transport failure never sets `failed` before the cap; only an explicit refusal from the target does |
| `sweep_fiscal_documents` | cron, every 5 minutes | Enqueues per-document jobs whose own lock dedupes, so an overlapping sweep is a no-op | Picks up documents whose `next_attempt_at` is due, re-queries documents stuck in `sent` past the dwell (a re-query, never a re-send), and runs **the orphan check** — a closed `counter` sale with no document row, recorded after `configured_at` — which is reported on the work list as a defect and never silently repaired |
| `export_fiscal_documents` | cron, on the configured cadence, only for a file target | `(tenant_id, period)`. A re-run overwrites the same file with the same content and includes exactly the same documents | Writes the period's file to object storage and moves each included document to `acknowledged`. A storage failure leaves every document where it was and retries on the next run; a partial file is never published |
| `notify_failed_deliveries` | cron, daily per tenant | `(tenant_id, date)` — a re-run on the same day notifies nobody twice | Sends one digest through S0's email path to the recipients in the settings group, naming the count and linking to the work list. **The work list is the record and the email is a pointer to it**; a failure that exists only in an inbox is a failure nobody resolved |

**Document creation is not a job.** The row is written in the same pinned transaction that applies the sale on push, through the sale handoff service; only delivery is asynchronous. A sale that lands with no document row while a target is configured is the one failure this stage cannot detect from the outside — every count here is a count of documents — so creation is transactional and the orphan check exists as the backstop. If that is wrong, the symptom is a marginally slower push. If the alternative is wrong, the symptom is a day of sales the client's invoicing system never heard of, discovered at month end.

## UI

Design-system sections are binding and are cited inline. Office surfaces render at Standard density (§B.4.1) and answer `j`, `k`, `Enter`, `x`, `Esc` and `/` (§B.13.2). Every status renders its family plus its label, never colour alone (§B.7.3).

**Nothing in this stage renders on a till.** That is a change from the earlier draft and it is stated because a reader of it will look for what is gone: there is no numbering read-out in the sync panel, no `blocked` banner, and no contingency line on the receipt. The receipt at `Cobrar` carries `sales.number` and claims nothing about any fiscal document, exactly as S4 already draws it.

**1 · `Envíos a facturación` — the work list.** An office route at `/fiscal-documents`, filter bar plus table plus record panel, in Existencias' shape. **It has no nav item** — §B.8.1 caps the sidebar at seven and Botica is at it — so it is reached from three places: the Panel's strip (S9), a `Ver envíos` action in the settings section, and the link in the digest email. **The measurement that would change that:** if a pilot's administrator opens it more than weekly, it earns a nav item.

A segmented control at the head holds three lists: `Pendientes` · `Fallidos` · `Ventas sin enviar`. Columns on the first two: `Sede 12 · Venta 14 · Destino 12 · Creado 12 (der.) · Intentos 8 (der.) · Motivo 24 · Estado 18`. `Estado` is the surface's one badge column (§B.7.3) and takes §B.7's grammar with these four values, whose **label text is this document's and is canonical**:

| Value | Family | Dot | Label | Reading |
|---|---|---|---|---|
| `pending` | Neutral | hollow | **Pendiente de envío** | Built, queued, nothing has failed |
| `sent` | Informative | **hollow** | **Enviado** | Delivered; the target has not confirmed yet. Hollow because we are waiting on something outside this system (§B.7.2) |
| `acknowledged` | Positive | solid | **Confirmado** | The target holds it. Terminal success, and a statement about the target and not about the DIAN |
| `failed` | Critical | solid | **Falló el envío** | An administrator's work list, never a cashier's |

**Those four strings — `Pendiente de envío`, `Enviado`, `Confirmado`, `Falló el envío` — are the canonical copy for `fiscal_documents.status`**, rendered verbatim wherever the value appears: this work list, the record panel, the settings read-out and any surface a later stage adds. **The split, recorded here so it is not re-litigated: the design system owns the status family and the dot treatment (§B.7.1, §B.7.2, §B.7.4); this document owns the label text.** The families and dots in the table above are §B.7's, reproduced so this surface can be built from one page rather than restated as a competing rule — `sent` carries the hollow dot because the system is waiting on something outside itself (§B.7.2). **And no label may claim a DIAN outcome**: `Confirmado` says the client's invoicing system holds the document and stops there, because Botica never learns what the DIAN did with it (§8, A9). A string that reads `Aceptado por la DIAN` on a row we handed to an API is a claim about a filing this product did not perform and cannot see.

`Motivo` is 12px `#727272` and always says something a person can act on: `El sistema de facturación respondió 503`, `El adquiriente no tiene número de documento`, `No hay conexión con el sistema de facturación`. The record panel carries the canonical payload as sent — or as it renders now, for a `failed` row, which is what an administrator needs to see before pressing anything — the target and its mapping version, the attempt trail, the target's parsed response, the identifiers it returned, and the footer `[Reintentar]`. The third list is sales, not documents, so it carries no badge column; each row states in words why no document exists.

- Loading: geometry-matched skeleton rows at the real 48px height and real column widths; a re-fetch after a filter change dims existing rows to `opacity:0.6` with the 2px progress line rather than blanking them (§B.10.1).
- Empty, **and there are two kinds here that must not be conflated**: with a target configured and nothing pending, the deliberately-empty kind — `No hay envíos pendientes` with `47 documentos confirmados hoy.` as the body and no primary action (§B.10.2). With **no target configured**, the never-populated kind in the neutral family — `No hay ningún sistema de facturación conectado`, a body naming what would fill it, and `Configurar` as the primary, pointing at the settings section. No count, no warning family, no badge (§8). Filtered-to-nothing takes the second kind with a secondary `Quitar filtros`.
- Error: route scope, with a retry and a selectable correlation id (§B.10.3). A failing row keeps its place with the critical badge and its reason in the panel; **the row never turns red**.
- Denied: a `cashier` is not offered the route and a direct link refuses inside the content region, naming the role required (§B.8.3).

**2 · `Ajustes · Facturación electrónica`.** A section of the settings dialog under **Operación** (§B.8.4·4), opened at `/{route}?settings=invoicing` so it is a link and `Escape` returns where you were. **This is the only surface in the product that ever mentions the handoff being off.**

With nothing connected, the section is one paragraph and one action: `Botica no está conectado a ningún sistema de facturación. Las ventas se registran normalmente; no se envía ningún documento.` and `Conectar sistema` — stated in the neutral family, as a fact and not as a problem. With a target connected, blocks separated by space and hairlines: **Sistema** (target and environment as selects, base URL where the target needs one, the credential shown only as `Configurada en el servidor` with no field to type one into, and `Desconectar` as a destructive confirm naming the consequence); **Mapeo** (which mapping and version, plus `Ver documento de ejemplo`, which renders the canonical payload of the most recent sale through `GET /api/sales/{id}/canonical-document` in a 720px modal with the JSON selectable — this is the control that makes wiring a client's system a conversation with evidence in it); **Entrega** (per-sale or batched, the cadence, and the file destination for a file target); **Reintentos** (the ladder cap, the dwell, the skew tolerance and the digest recipients); and a 24-hour read-out — `enviados`, `pendientes`, `fallidos` — with `Ver envíos`.

- Loading: skeleton of the real field stack. Error: field scope for validation, inline region scope for a rejected save — and a saved target whose credential does not resolve is a field-scope error on the target block, because a target that cannot authenticate is not configured (that is the predicate, and the screen says so rather than reporting success and failing silently at 3 a.m.).
- Denied: `cashier` never reaches the dialog; `admin` sees the **Sistema** block read-only.

**3 · The sale's fiscal read-out.** Rendered inside S4's sale detail and its reprint. When the target returned identifiers, one line: `Factura del sistema de facturación · FE-4471` with the PDF link when one exists. When it returned nothing, or when the handoff has not landed, or when no target is configured, **the region renders nothing at all** — not a placeholder, not a status, and never a skeleton that will not resolve (§B.9.2 tier 3). At counter density it carries no badge in any state (§8).

**4 · The Panel.** S9's, not this stage's. S5 supplies `GET /api/fiscal-documents/summary`; S9 renders a strip when there is something to say and **nothing whatsoever when `configured` is false**.

## Offline

**The handoff queues alongside the sale, is asynchronous, and is never on the counter's critical path.** Nothing at `Cobrar` waits for it, touches it or mentions it, which is why a legal obligation moving to the client's system costs the counter nothing and why §4's 200 ms budget is measured the same before and after this stage ships.

**With no target configured there is nothing to queue.** The sale closes, the receipt renders, and no row is written anywhere.

**Nothing in this stage is local.** No table of S5's reaches a device, no registry amendment is requested (rule 9), and the delta pull's `< 20 ms` p95 and the counter's `< 2.5 s` cold start are unchanged by this stage — measurable before and after, and they must not move.

**What happens with no network, in order:** the sale closes on the till; the receipt renders with `sales.number`; nothing fiscal exists anywhere, and the cashier is told nothing because there is nothing to tell. On reconnection the push lands the sale, and inside that same pinned transaction the handoff service writes the `pending` row and enqueues delivery. The job drains the queue. Nobody is asked what to do about any of it, which is what "reconciles when the connection returns without anyone being asked" means (§5).

**A blackout on the server's side of the wire is the same event.** The client's invoicing system being unreachable leaves rows in `pending`, the ladder retrying and the office watching a count. The customer left with their receipt some time ago.

**Clock skew.** `occurred_at` is the till's wall clock and is what the customer's receipt says; `recorded_at` is the server's and is what every count in this stage uses (§5 rule 4, rule 8). Both travel in the payload. When `occurred_at` sits outside the configured tolerance of `recorded_at`, the document is **held rather than delivered** — `next_attempt_at` null, the reason in `error`, on the work list — because a document dated two days wrong at the far end is a correction someone makes by hand, and it is cheaper to hold one than to unwind one.

**`blocked` has no producer here, and none anywhere.** The earlier draft's single interruption — a device that exhausted its numbering lease offline — is gone with numbering (A6). The state stays defined in §B.9.1 because the day a till must stop is the day nobody wants to be designing the message, and at v1 nothing raises it (§5).

## Acceptance

Each of these passes or fails while someone watches. Budgets are §4's, measured on the pilot's hardware.

1. **Unconfigured, and this is the first test because it is the default.** With no target in the settings group, fifty sales close across two sedes: `fiscal_documents` has zero rows, the job table has zero entries, `GET /api/fiscal-documents/summary` answers `{"configured": false}` with no counts, the Panel renders no fiscal element of any kind, and no error, warning or badge appears on any surface — verified by walking the till, the Panel, the nav and the inbox.
2. The only surface naming the state is `Ajustes · Facturación electrónica`, and the work-list route renders the never-populated empty state with `Configurar`. A grep of the built bundle finds the string nowhere else.
3. Configuring a target stamps `configured_at`. Sales closed before it produce no documents and appear in no list; every sale closed after it produces exactly one `pending` row.
4. A sale with three lines across `excluded`, `rate_5` and `rate_19` renders a payload whose per-line tax amounts, `tax_by_class` bases and totals equal `sales.subtotal`, `discount`, `tax` and `total` to the peso. A payload whose lines do not reconcile is written `failed` and is never sent.
5. A sale with no customer renders a complete acquirer block with `is_final_consumer`; a sale with a customer renders document type, number and name.
6. A sale made with the network interface disabled, to a customer registered at that same offline till, renders a complete acquirer block on reconnection with no field missing.
7. A sale whose customer has no document number lands `failed` with `El adquiriente no tiene número de documento` on the work list; **the cashier's screen showed nothing at any point**; correcting the customer and pressing `Reintentar` rebuilds the payload from the corrected sale and reaches `acknowledged`, with no edit to the fiscal row and no migration.
8. **Exactly once.** The connection is killed after the target has committed. The next attempt queries by `document_key`, finds the document, and moves to `acknowledged` without sending. The target is inspected directly and holds exactly one document for that sale.
9. A target whose mapping declares neither an idempotency field nor a `query` operation is capped at one attempt: an ambiguous timeout lands on the work list for a human rather than being retried blind.
10. Two concurrent calls of the handoff service for one sale produce one row; the `UNIQUE (tenant_id, document_key)` constraint is what makes the second a no-op rather than a race the job wins.
11. A return produces a credit note referencing the original's `sale_number` and `document_key`, carrying only the returned lines at the prices originally charged. Its stock movement is S4's and exists independently of it.
12. A void of an `acknowledged` sale produces a credit note; no row is deleted or edited. A void of a sale whose document has not left delivers the sale first and the credit note second, in that order, and the target holds both.
13. An `imported` sale from S6's loader produces no document, and the service refuses one when called directly.
14. Killing the target mid-delivery leaves the document `pending`, never `failed`; `attempts` increments and `Motivo` names the transport failure. Restoring the target drains the queue with no manual step.
15. **A second target is added as a mapping plus credentials, and the diff touches no file under the sale, the ticket, the sync path or the canonical builder** — checked by reading the diff, not by asserting it.
16. The file export writes a period's file containing each document exactly once keyed by `document_key`; each is `acknowledged`; a re-run of the same period overwrites the file with identical content and adds nothing; a document in an earlier period's file never appears in a later one.
17. `GET /api/fiscal-documents/summary` answers in **< 50 ms p95**, and its unsent figure matches a hand count of `pending + sent`.
18. Every elevated mutation — the settings PATCH, a forced retry, a disconnect — lands an `audit_log` row with actor, entity and before/after (ledger).
19. A `cashier` sees no entry for the work list and a direct link refuses inside the content region naming the role required (§B.8.3). A second tenant on the same instance reads zero rows of `fiscal_documents`.
20. **This stage adds nothing to the till.** The delta pull with nothing pending stays **< 20 ms p95** and counter cold start stays **< 2.5 s**, measured before and after this stage ships; `Cobrar` to closed ticket stays **< 200 ms p95** with the target unreachable and with the network disconnected entirely.
21. No path in this stage puts a till in `blocked`, and no surface in it renders that state.

## Hands off

- **`fiscal_documents`, the `fiscal_document_status` enum, and `UNIQUE (tenant_id, document_key)`** — the exactly-once invariant lives in the database, and the key is derived from the sale so it cannot drift.
- **The sale handoff service**, the ledger's sixth cross-stage service: given a closed sale or a return and the caller's already-open pinned transaction, it writes at most one `pending` row keyed by `document_key` and enqueues delivery behind the commit. **S4 passes in** the `sales` or `sale_returns` row and its transaction — no payload, no target knowledge, no delivery call. **S4 gets back** the row, or **nothing at all when no target is configured, which S4 treats as the normal case rather than an error**. The service refuses a `sales.source = imported` row (ledger, disputed columns) and is the only writer of `fiscal_documents`.
- **The canonical sale document builder** — a pure function of the sale and the mapping version, validated once for every target. Other stages may read the payload contract; none builds its own (§8, A9).
- **The target interface behind one boundary** — `deliver`, `query`, optional `fetch_representation` — plus the declarative mapping. No other module in the system knows a target's name, its payload shape or its error vocabulary, and a proveedor tecnológico is one target among others rather than a case in the design.
- **`GET /api/fiscal-documents/summary` for S9, with `configured: false` meaning render nothing** — no strip, no clause, no placeholder, the figure **absent rather than zero or a label** (§8), which is what S9 specifies and what the body's shape enforces: an answer that carries no counts cannot be rendered as one. When `configured` is true, S9's existing region-strip contract holds unchanged, at the same < 50 ms budget, and the unsent figure is the one Panel number that does not come from `daily_metrics` because §8 asks *"is anything stuck right now?"* and a rollup cannot answer *right now*.
- **The `invoicing` settings group**, written through S0's rule-5 helper, with the credential deliberately absent from it and `configured_at` as the boundary that makes "no backfill" a fact rather than a convention.
- **No sync-registry amendment, and no till surface.** The counter is untouched by this stage. A future stage that puts anything of S5's on a device amends S2's registry first (rule 9).
- **This stage blocks S9 and nothing else.** S6, S7 and S8 fan out from S4 alongside it and none waits on a document (§13): an `imported` sale never produces one, a price change reaches a document only through `sale_lines.unit_price` which is already stamped, and a suggestion-originated line is fiscally indistinguishable from any other. A tenant with no invoicing system connected still gets a correct suggested order, a correct price proposal and a correct suggestion.

## Gated on

**§11.1 — which system each client invoices with, and what its API expects. This is one mapping per target and it is not a blocker** (§11.1). The canonical document, the delivery, the exactly-once mechanism, the work list and the settings surface are all built regardless, and the first mapping is written against the first client's system. Six questions are asked of every target, and each is answered in a mapping rather than in code:

1. **The envelope and the field names**, including how it names an emitter, an acquirer and a line.
2. **Which field carries `document_key`** as an idempotency key or external reference — and if there is none, whether it offers a `query` operation. A target with neither is delivered to once per document, by policy.
3. **The identifier it expects for an unidentified acquirer** — our `is_final_consumer` has to become something concrete.
4. **Its vocabularies** for payment method, document type and tax class.
5. **Whether it acknowledges synchronously or later**, and how a later confirmation is fetched.
6. **Credentials and their custody** — where the key lives, who rotates it, and what a `test` environment looks like.

**What must not happen is anyone promising DIAN transmission**, which Botica does not do (§8, A9, §12).

**§11.5 — the pilot's hardware, connectivity and browser.** It blocks verification of acceptance criteria 17 and 20: every budget in §4 is measured on those machines, and "works on the developer's Mac" is not a result.

**§11.6 — which sede goes first**, and therefore which invoicing system the first mapping is written against.

### The numbering-lease design, preserved and not built

**A6 defers this and no migration contains it.** It is kept here, in one paragraph, because it is the shape this stage would take on the day Botica issues, and rediscovering it costs more than recording it.

A resolución de facturación is granted by the DIAN **per sede**, as a prefix and a numeric range with a validity window (`dian_resolutions`). Every number ever issued would have to lie inside exactly one **lease** (`numbering_leases`): a contiguous block carved out of a resolution's range and granted to one device, non-overlapping by an exclusion constraint on `(resolution_id, [range_from, range_to])` rather than by application code, with `consumed_through` advanced on the server **when the device reports what it issued and never when the block is granted**. The device allocates the next number in its block inside the same local transaction that closes the sale, after the total is final — **with no second, online allocation path**, because a contingency mechanism exercised only during a blackout is a mechanism that is broken during a blackout. A released remainder — a device retired, lost or wiped — is burned and never re-granted to another device, since re-granting a block a device may still be holding is how two documents acquire the same number, which no credit note corrects. Capacity and expiry would be alerted days ahead, never discovered at a counter.

**Why it is the right design and still not built:** it is the only mechanism that lets an offline till produce a bare consecutive legal number without colliding with the till beside it. It is unnecessary the moment the client's system issues, because then the client's system numbers, and Botica allocates only its internal `sales.number` (§8, A6). **What would bring it back:** a decision that Botica issues — at which point this stage grows a numbering module, a resolution table, an adapter that signs, and a very different *Gated on*.

**One consequence travels with the deferral, and it reaches beyond this stage.** The sync state `blocked` existed for exactly one condition: a device that exhausted its numbering lease while offline. With numbering deferred, **`blocked` has no producer at v1** (§5, A6). It stays defined — the day a till genuinely must stop is not the day to design that message from scratch — and nothing raises it, in this stage or in any other.

### Ledger and design-system notes

**`ownership.md` wins and this document may not amend it unilaterally.** Both entries the re-scope touched are current there and this stage asks it for nothing: the `devices` row names S2 alone, and the cross-stage service is the **sale handoff service**, whose contract this document is written to.

**`design-system.md` is current on every fiscal surface**, checked rather than assumed: §B.7.4 carries the four handoff states framed as the handoff rather than five DIAN ones and takes its labels from this stage; §B.9.1 marks the `blocked` state string unwritten and binds the copy to the first stage that ever raises it; §B.9.3 states that the sync panel carries no fiscal read-out of any kind; and §B.10.3's required-shape example names the client's invoicing system and no DIAN outcome. Nothing on this stage's surfaces is waiting on a design-system change.

### Names coined here

Because neither `architecture.md` §3 nor the ledger supplies them, and each is argued at its point of use: the columns `fiscal_documents.location_id`, `next_attempt_at` and `mapping_version`; the composition of `document_key` as `{location.code}-{sales.number}` with `-NC{n}` for a credit note; the canonical payload's own field names, which are ours and are the contract every mapping translates from; the settings keys under the `invoicing` group, including `configured_at`; the API paths above, of which `GET /api/sales/{id}/canonical-document` is the one that is not obvious and earns its place by making a mapping writable in an afternoon; the route name **`Envíos a facturación`**; and the four Spanish status labels **Pendiente de envío**, **Enviado**, **Confirmado** and **Falló el envío** — the strings only, the family and the dot each renders in being §B.7's (see *UI*). Every identifier is English and every interface string is Spanish (§3).

**Dropped deliberately, and named so their absence is not read as an oversight:** the `fiscal_document_type` enum, the acquirer snapshot columns, `issued_at`, `provider_reference`, `replaces_id`, `resolution_id`, `lease_id`, the provider-callback endpoint and the counter key `F6 · Identificar cliente`. Each existed to serve numbering, transmission or a rejection that could not be corrected — and none of those exists here. The acquirer is still captured at the counter and still works offline; that control is S4's, in the cobro dialog it already draws.

**One design gap this stage does not close alone.** §B.17 item 5 records that the payment flow after `Cobrar` is undrawn, and names the customer-identification step inside it. S5's only requirement of that flow is that document type, number and name are capturable at the counter and offline, which S4 already specifies. The flow's layout needs a design pass with S4 before either stage builds the cobro dialog, or the two stages will draw it twice.
