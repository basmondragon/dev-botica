import { useCallback, useEffect, useRef, useState } from "react";
import type { Me } from "@/api/queries";
import type {
  BarcodeDoc,
  CustomerDoc,
  ItemDoc,
  LotDoc,
  PolicyDoc,
  SaleDoc,
  SaleLineDoc,
  ShiftDoc,
} from "@/sync/registry";
import type { SyncDatabase } from "@/sync/store";
import { useSync } from "@/sync/context";
import { findCustomers, searchCatalog } from "@/sync/local";
import { RegisterCustomer } from "@/sync/register-customer";
import { SyncPanel } from "@/sync/sync-panel";
import { SyncStatus } from "@/sync/sync-status";
import { Content, TopBar, TopBarButton } from "@/shell/shell";
import { DOT, time } from "@/ui/format";
import { RegionError } from "@/ui/states";
import { COUNTER_INSET } from "@/ui/inset";
import { cn } from "@/ui/cn";
import { Cobro, Receipt, type Applied, type CustomerPick } from "./cobro";
import { Devolucion, FindSale, type ReturnLineDraft } from "./devolucion";
import { LotPicker, PriceOverride } from "./line-controls";
import { CaptureField, SearchColumn, SearchResults, type Hit } from "./search";
import { AssistantColumn } from "@/assistant/panel";
import {
  refreshBundle,
  readBundle,
  knowsTheCatalog,
  type Bundle,
} from "@/assistant/bundle";
import { extract, type Fact } from "@/assistant/extract";
import * as assistant from "@/assistant/pipeline";
import {
  mintOffer,
  offerPayload,
  queueClose,
  forgetAsked,
  queueOffer,
  queueSupersede,
  rememberCredit,
  rememberQuery,
  type Offer,
} from "@/assistant/local";
import { ask } from "@/api/assistant";
import { useSettingsDialog } from "@/settings/use-settings";
import { TicketPanel, type TicketLine } from "./ticket";
import { CloseShift, OpenShift, type CloseReport } from "./turno";
import {
  freshCadence,
  looksScanned,
  noteKeystroke,
  useCapture,
  useTillKeys,
} from "./capture";
import { toCents } from "./money";
import * as till from "./local";

/**
 * How long card B waits for prose before resolving to the local
 * recommendation. It is the client's own ceiling and sits above the
 * `model_timeout_ms` the server enforces: the server gives up first, and this
 * is what covers a request that never reaches it at all.
 */
const ASSISTANT_TIMEOUT_MS = 6000;

/**
 * `Mostrador · Venta` — the till.
 *
 * **Nothing on this surface touches the network**, and that is the criterion
 * rather than a property: a keystroke filters a local list, a scan resolves
 * against a local barcode index, `Cobrar` writes locally and the receipt paints
 * from what was just written. The cable can be out of the wall for the whole
 * shift and the only thing that changes is the line in the header.
 *
 * The left column is the search list **until S8 takes it**: the transcript, the
 * recommendation and the suggestion cards move in and this same list re-renders
 * as an overlay anchored under the capture field.
 */
export function Till({ me }: { me: Me }) {
  const sync = useSync();
  const database = sync.database;
  const device = sync.device;

  const field = useRef<HTMLInputElement>(null);
  const cadence = useRef(freshCadence());
  /** The ticket a concurrent `add` is opening, before React has seen it. */
  const opening = useRef<till.Ticket | null>(null);

  const [shift, setShift] = useState<ShiftDoc | null>(null);
  const [ticket, setTicket] = useState<till.Ticket | null>(null);
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [unresolved, setUnresolved] = useState<string | null>(null);
  const [items, setItems] = useState<Map<string, ItemDoc>>(new Map());
  const [lots, setLots] = useState<Map<string, LotDoc>>(new Map());
  const [average, setAverage] = useState<number | null>(null);
  const [referenceCount, setReferenceCount] = useState(0);

  const [cobro, setCobro] = useState(false);
  const [receipt, setReceipt] = useState<{
    sale: SaleDoc;
    lines: SaleLineDoc[];
    payments: Applied[];
    change: number;
  } | null>(null);
  const [closing, setClosing] = useState(false);
  const [closeReport, setCloseReport] = useState<CloseReport>({
    openingFloat: 0,
    cashSales: 0,
    cashReturns: 0,
    expected: 0,
  });
  const [registering, setRegistering] = useState(false);
  const [customers, setCustomers] = useState<CustomerDoc[]>([]);
  const [attached, setAttached] = useState<CustomerPick | null>(null);
  const [finding, setFinding] = useState(false);
  const [findValue, setFindValue] = useState("");
  const [findMissed, setFindMissed] = useState(false);
  const [recent, setRecent] = useState<{ sale: SaleDoc; label: string }[]>([]);
  const [returning, setReturning] = useState<{
    ticket: till.Ticket;
    drafts: ReturnLineDraft[];
    method: string;
    voidable: boolean;
  } | null>(null);
  const [lots_, setLots_] = useState<{
    entry: TicketLine;
    options: till.LotOption[];
  } | null>(null);
  const [pricing, setPricing] = useState<TicketLine | null>(null);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  /**
   * S8 · the assistant column. **Every one of these lives in this component and
   * not in the store**: a query is a question asked of a customer who is
   * standing there, and the one durable record of it is the outbox row queued
   * the moment its cards were drawn.
   */
  const transcriptField = useRef<HTMLTextAreaElement>(null);
  const [bundle, setBundle] = useState<Bundle | null>(() => readBundle());
  const [transcript, setTranscript] = useState("");
  const [facts, setFacts] = useState<Fact[]>([]);
  const [asked, setAsked] = useState(false);
  const [cards, setCards] = useState<assistant.Card[]>([]);
  const [surviving, setSurviving] = useState(0);
  const [seeded, setSeeded] = useState(0);
  const [offer, setOffer] = useState<Offer | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [prose, setProse] = useState({
    primary: "",
    secondary: "",
    local: true,
    loading: false,
  });
  const settingsDialog = useSettingsDialog();

  const focusCapture = useCallback(() => {
    field.current?.focus();
  }, []);

  /** Any dialog suspends the capture redirect: **a scan while `Cobro` is open
   *  does nothing**, because a barcode arriving during payment is a cashier
   *  scanning the next customer's item. */
  const suspended =
    cobro ||
    !!receipt ||
    closing ||
    registering ||
    finding ||
    !!returning ||
    !!lots_ ||
    !!pricing ||
    loading ||
    !shift;

  useCapture(field, suspended);
  useTillKeys({
    suspended,
    cobrar: () => {
      if (ticket && ticket.lines.length > 0) setCobro(true);
    },
    focusCapture,
    syncPanel: () => sync.setPanelOpen(!sync.panelOpen),
    // **`Esc` clears the capture field and never cancels the sale.**
    clear: () => {
      setTerm("");
      setUnresolved(null);
      setHits([]);
    },
  });

  /**
   * The turno, the ticket in progress, the average-ticket note and the
   * reference count — all four from the local store, all four surviving a
   * relaunch, and all four the same for two tabs on one till.
   *
   * It **reads** and returns rather than setting state as it goes, so the one
   * place that applies the answer can drop it when the surface has moved on.
   */
  const read = useCallback(async () => {
    if (!database || !device) return null;
    const held = till.heldTicketId();
    const ticket = held ? await till.readTicket(database, held) : null;
    return {
      shift: await till.openShift(database, device),
      ticket,
      // **The ticket's own products, read back with it.** A ticket survives a
      // relaunch and is shared by a second tab, and neither of those went
      // through `add()` — so without this the restored lines render as
      // `Producto` with no flags, no lot control and no prescription
      // acknowledgement at `Cobrar`, which is the one of the three that
      // matters.
      catalog: ticket ? await namesFor(database, ticket) : null,
      average: await till.averageTicket(database),
      references: await database.collections.items!.count().exec(),
    };
  }, [database, device]);

  const apply = useCallback((next: Awaited<ReturnType<typeof read>>) => {
    // §B.10.1 · the skeleton comes down when the store has answered, whatever
    // it answered — a panel that stayed on its skeleton because the till has no
    // rows yet is the one loading state the design system forbids outright.
    setLoading(false);
    if (!next) return;
    setShift(next.shift);
    setTicket(next.ticket);
    const catalog = next.catalog;
    if (catalog) {
      setItems((held) => new Map([...held, ...catalog.items]));
      setLots((held) => new Map([...held, ...catalog.lots]));
    }
    setAverage(next.average);
    setReferenceCount(next.references);
  }, []);

  useEffect(() => {
    let stale = false;
    void read().then((next) => {
      if (!stale) apply(next);
    });
    return () => {
      stale = true;
    };
  }, [read, apply]);

  // Two tabs on one till show one ticket, not two: the pointer lives in
  // `localStorage`, and the browser tells the other tab when it moves.
  useEffect(() => {
    function onStorage() {
      void read().then(apply);
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [read, apply]);

  const refreshTicket = useCallback(async () => {
    if (!database) return;
    const held = till.heldTicketId();
    setTicket(held ? await till.readTicket(database, held) : null);
  }, [database]);

  // **No debounce.** Debouncing exists to spare a server and this path has no
  // server on it; a debounce would add to the very budget §4 sets at 30 ms.
  useEffect(() => {
    const trimmed = term.trim();
    if (!database || !device || !trimmed) return;
    let stale = false;
    void resolveHits(database, device.location_id, trimmed).then((rows) => {
      if (!stale) setHits(rows);
    });
    return () => {
      stale = true;
    };
  }, [database, device, term]);

  // An empty field shows the deliberately-empty state, so the last search's
  // rows are dropped at render rather than cleared by an effect that would run
  // one frame late.
  const visible = term.trim() ? hits : [];

  const cache = useCallback(async (item: ItemDoc, lot: LotDoc | null) => {
    setItems((held) => new Map(held).set(item.id, item));
    if (lot) setLots((held) => new Map(held).set(lot.id, lot));
  }, []);

  const add = useCallback(
    async (item: ItemDoc, quantity: number): Promise<SaleLineDoc | null> => {
      if (!database || !device || !shift) return null;
      const price = await till.priceOf(database, item.id);
      if (price === null) {
        setFailure(
          `${item.name} no tiene precio vigente en esta sede, así que no se puede agregar al tiquete.`,
        );
        return null;
      }
      const queue = item.tracks_lots
        ? await till.lotQueue(database, device, item.id)
        : [];
      // **FEFO by default and without the cashier choosing.** Where the sede
      // holds no lot at all the line takes none and the sale still closes: a
      // sale is never refused for want of a lot, and the exception is raised to
      // the office after the push (§5 rule 2).
      const head = queue[0] ?? null;
      // **One ticket, however fast the scans arrive.** Two bursts a frame
      // apart would both read `ticket` as null and open two sales — or worse,
      // two lines at position 0 — so the ticket being opened is held in a ref
      // the moment the write starts, where the next call can see it before
      // React has re-rendered.
      let current = ticket ?? opening.current;
      if (!current) {
        const started = till.startTicket(database, device, shift, {
          id: me.id,
          name: me.name,
        });
        opening.current = { sale: await started, lines: [] };
        current = opening.current;
      }
      const line = await till.addLine(database, current, {
        item,
        quantity,
        lot: head?.lot ?? null,
        unitPrice: price,
        unitCost: head?.lot?.unit_cost ?? null,
        fefoOverride: false,
      });
      till.markOverride(line.id, false);
      await cache(item, head?.lot ?? null);
      opening.current = null;
      await refreshTicket();
      setTerm("");
      setUnresolved(null);
      setFailure("");
      focusCapture();
      return line;
    },
    [database, device, shift, ticket, me, cache, refreshTicket, focusCapture],
  );

  /**
   * S8 · the bundle, cached with the device record.
   *
   * **A failure is not an event.** With no connection the till extracts against
   * the bundle it already holds, which is the whole point of caching it; with
   * none at all card C renders the configuration state, which is the honest
   * reading and not the cold-start floor.
   */
  useEffect(() => {
    if (!device) return;
    let stale = false;
    void refreshBundle(device).then((next) => {
      if (!stale && next) setBundle(next);
    });
    return () => {
      stale = true;
    };
  }, [device]);

  const assistantOn = bundle === null || bundle.enabled;

  /**
   * Steps 2 to 5, on the device, with **no request anywhere on the path**.
   *
   * The offer rows are queued the moment the cards exist, so the acceptance
   * rate has a denominator whether or not this till ever reaches a server
   * again. Card B's prose is asked for afterwards and is awaited by nothing:
   * the local recommendation is already on screen, and where the model is off,
   * capped, unreachable, slow or rejected it simply stays there under the
   * `MODO LOCAL` eyebrow.
   */
  const runAssistant = useCallback(
    async (next: Fact[]) => {
      if (!database || !device || !bundle) {
        setFacts(next);
        setAsked(true);
        return;
      }
      setFacts(next);
      setAsked(true);
      // The cashier re-asked on the same open sale: the previous query's
      // un-accepted suggestions leave the denominator.
      if (offer) await queueSupersede(database, offer.id);

      const held = till.heldTicketId();
      const lines = held
        ? ((await database.collections
            .sale_lines!.find({ selector: { kind: "line", parent_id: held } })
            .exec()) as unknown as SaleLineDoc[])
        : [];
      const outcome = await assistant.run(database, {
        locationId: device.location_id,
        facts: next,
        bundle,
        ticketItemIds: lines
          .map((line) => line.item_id)
          .filter((one): one is string => !!one),
      });
      setCards(outcome.cards);
      setSurviving(outcome.candidateCount);
      setSeeded(outcome.seededCount);
      setAdded(new Set());

      const local = assistant.localProse(
        outcome.cards,
        outcome.candidateCount,
        bundle,
      );
      setProse({ ...local, local: true, loading: outcome.cards.length > 0 });

      // **A question with no candidates is still a question that was asked.**
      // `assistant_queries` is one row per question at a counter, so the row is
      // written whether or not the shelf had anything to answer with — it is
      // what `Registro del asistente` reads and what the chipless share is
      // measured over, and it contributes nothing to either side of the rate.
      const minted = mintOffer(outcome.cards);
      setOffer(minted);
      // **Every query is attached to the ticket it was asked during**, whether
      // or not a card was taken: a denominator that counted only the questions
      // somebody acted on is a rate that reads 100%.
      rememberQuery(minted.id);
      const payload = offerPayload(minted, {
        facts: next,
        transcript,
        candidateCount: outcome.candidateCount,
        excluded: outcome.excluded,
        bundleVersion: bundle.version,
        saleId: held,
        recommendation: local.primary,
        recommendationSecondary: local.secondary,
        retainTranscript: bundle.retain_transcripts,
        userId: me.id,
        userName: me.name,
      });
      await queueOffer(database, payload);
      if (outcome.cards.length === 0) return;

      // **Not awaited.** The cards are already painted and card B already has
      // its local recommendation; the prose arrives when it arrives, and
      // `Enter` gives the capture field back to the cashier at once rather
      // than in six seconds' time (§B.13.3).
      void ask(device, payload, ASSISTANT_TIMEOUT_MS).then((answer) => {
        if (!answer || answer.mode !== "model") {
          setProse((held_) => ({ ...held_, loading: false }));
          return;
        }
        setProse({
          primary: answer.recommendation,
          secondary: answer.recommendation_secondary,
          local: false,
          loading: false,
        });
        const better = answer.reasons ?? {};
        setCards((held_) =>
          held_.map((card) =>
            better[card.item.id]
              ? { ...card, reason: better[card.item.id]! }
              : card,
          ),
        );
      });
    },
    [database, device, bundle, offer, transcript, me],
  );

  const commitTranscript = useCallback(() => {
    void runAssistant(extract(transcript, bundle)).then(focusCapture);
  }, [runAssistant, transcript, bundle, focusCapture]);

  /**
   * **Removing a chip re-runs the whole pipeline including the filter.** A chip
   * is not a display artefact, it is the filter's input — a screen where
   * deleting *fiebre* leaves the item still filtered is a screen that has
   * stopped telling the truth.
   */
  const removeChip = useCallback(
    (fact: Fact) => {
      void runAssistant(
        facts.filter(
          (one) => !(one.kind === fact.kind && one.key === fact.key),
        ),
      ).then(focusCapture);
    },
    [runAssistant, facts, focusCapture],
  );

  /**
   * `Agregar` · the line goes on optimistically with no loading state
   * (§B.10.1), the card stays on screen with its button in the added state, and
   * focus returns to the capture field so the next barcode scan lands in it
   * (§B.13.3).
   */
  const addSuggestion = useCallback(
    async (card: assistant.Card) => {
      if (!database || !offer) return;
      const line = await add(card.item, 1);
      if (!line) return;
      setAdded((held) => new Set(held).add(card.item.id));
      const document = await database.collections
        .sale_lines!.findOne(line.id)
        .exec();
      await document?.incrementalPatch({ from_suggestion: true });
      const entry = offer.cards.find(
        (one) => one.card.item.id === card.item.id,
      );
      if (entry) {
        rememberCredit(line.parent_id, {
          suggestion_id: entry.id,
          query_id: offer.id,
          line_id: line.id,
        });
      }
      await refreshTicket();
      focusCapture();
    },
    [database, offer, add, refreshTicket, focusCapture],
  );

  const clearAssistant = useCallback(() => {
    forgetAsked();
    // **The transcript leaves the till when the sale closes** — a shared till in
    // a shop is not a place for a queue of other people's symptoms (§11.3).
    setTranscript("");
    setFacts([]);
    setCards([]);
    setAsked(false);
    setOffer(null);
    setAdded(new Set());
    setProse({ primary: "", secondary: "", local: true, loading: false });
  }, []);

  /**
   * The terminating `Enter`.
   *
   * The heuristic decides how the buffer was *produced*, never what it *means*:
   * an exact barcode match adds a line whether it was scanned or typed, and a
   * scan the heuristic missed still lands here. The only cost of getting it
   * wrong is the filtering work that ran on the way.
   */
  const commitCapture = useCallback(async () => {
    if (!database) return;
    const code = term.trim();
    if (!code) return;
    const scanned = looksScanned(cadence.current);
    cadence.current = freshCadence();

    const matches = (await database.collections
      .item_barcodes!.find({ selector: { code } })
      .exec()) as unknown as BarcodeDoc[];
    if (matches.length === 1) {
      const item = (await database.collections
        .items!.findOne(matches[0]!.item_id)
        .exec()) as unknown as ItemDoc | null;
      if (item) {
        await add(item, 1);
        return;
      }
    }
    if (matches.length > 1) {
      // A code on two items opens the list with both and adds nothing.
      setUnresolved(null);
      return;
    }
    if (scanned || /^\d{6,}$/.test(code)) {
      // **The code stays in the field** so the cashier can read it out. Never a
      // toast and never a dialog (§B.10.3).
      setUnresolved(code);
      return;
    }
    if (hits.length === 1) await add(hits[0]!.item, 1);
  }, [database, term, hits, add]);

  const cobrar = useCallback(
    async (applied: Applied[], change: number) => {
      if (!database || !ticket) return;
      setBusy(true);
      try {
        const closed = await till.commit(
          database,
          ticket,
          applied.map((one) => ({
            method: one.method,
            amount: one.amount,
            reference: one.reference,
          })),
          attached,
        );
        setReceipt({
          sale: closed.sale,
          lines: closed.lines,
          payments: applied,
          // **Tendered and change are display figures and are not stored**: the
          // sale was paid for with its total, however many notes crossed the
          // counter (§3).
          change,
        });
        // **Queued after S4's own lines and its sale**, which is what makes
        // the acceptance arrive behind the line it credits: `client_uuid` is
        // uuid v7 and the push applies a batch in that order.
        await queueClose(database, ticket.sale.id, closed.lines);
        clearAssistant();
        setCobro(false);
        setAttached(null);
        setTicket(null);
        setAverage(await till.averageTicket(database));
        setFailure("");
      } catch (error) {
        setFailure(
          error instanceof Error
            ? error.message
            : "El navegador rechazó la escritura local.",
        );
      } finally {
        setBusy(false);
      }
    },
    [database, ticket, attached, clearAssistant],
  );

  if (!database || !device) {
    return (
      <>
        <TopBar breadcrumb={["Mostrador"]} title="Venta" />
        <Content>
          <RegionError
            title="Este equipo todavía no tiene su copia local."
            detail="El mostrador se atiende desde una caja registrada. Registre este equipo en Ajustes · Sedes y dispositivos."
          />
        </Content>
      </>
    );
  }

  /** **One capture field, in the position it has always held** — the top of the
   *  left column. The code that matched nothing travels with it, kept **in the
   *  field** so the cashier can read it out — never a toast and never a dialog
   *  (§B.10.3). The assistant borrows the column below it; the field itself
   *  does not move, which is what keeps S4's scan path and criterion 27 intact
   *  whichever way `enabled` is set. */
  const captureField = (
    <>
      <CaptureField
        ref={field}
        value={term}
        invalid={!!unresolved}
        onChange={(next) => {
          noteKeystroke(cadence.current);
          setUnresolved(null);
          setTerm(next);
        }}
        onEnter={() => void commitCapture()}
        onEscape={() => {
          setTerm("");
          setUnresolved(null);
        }}
      />
      {unresolved ? (
        <p className="mt-1.5 text-12 text-critical">
          Código no encontrado {DOT} {unresolved}
        </p>
      ) : null}
    </>
  );

  const ticketLines: TicketLine[] = (ticket?.lines ?? []).map((line) => ({
    line,
    item: items.get(line.item_id ?? ""),
    lot: line.lot_id ? lots.get(line.lot_id) : undefined,
  }));
  const canPrice = me.role !== "cashier";

  return (
    <>
      <TopBar
        breadcrumb={["Mostrador"]}
        title={ticket ? `Venta ${ticket.sale.number}` : "Venta"}
        actions={
          <>
            <span className="text-11 text-ink-note">
              {shift
                ? `Turno abierto ${time(shift.opened_at)}`
                : "Sin turno abierto"}
            </span>
            <TopBarButton
              variant="secondary"
              onClick={() => {
                setFinding(true);
                void openFinder();
              }}
            >
              Buscar venta
            </TopBarButton>
            <TopBarButton
              variant="secondary"
              disabled={!shift}
              onClick={() => void beginClose()}
            >
              Cerrar turno
            </TopBarButton>
            <div className="relative">
              <SyncStatus placement="counter" />
              <SyncPanel className="absolute right-0 top-12" />
            </div>
          </>
        }
      />

      <main
        id="content"
        tabIndex={-1}
        className={cn(
          "flex min-h-0 flex-1 gap-5 overflow-hidden",
          COUNTER_INSET,
        )}
      >
        {assistantOn ? (
          <AssistantColumn
            field={captureField}
            /* **S4's list, re-rendered as an L3 overlay anchored under the
               capture field** — which is what S4's own component was built to
               do when this stage took its column. It appears only while there
               is something typed: an overlay carrying the deliberately-empty
               state would cover card A to say nothing. */
            overlay={
              term.trim() ? (
                <SearchResults
                  term={term}
                  hits={visible}
                  referenceCount={referenceCount}
                  locationName={device.location_name}
                  onAdd={(hit) => void add(hit.item, 1)}
                  onClear={() => {
                    setTerm("");
                    setUnresolved(null);
                    focusCapture();
                  }}
                />
              ) : null
            }
            asked={asked}
            transcript={transcript}
            facts={facts}
            cards={cards}
            surviving={surviving}
            locationName={device.location_name}
            added={added}
            primary={prose.primary}
            secondary={prose.secondary}
            local={prose.local}
            loading={prose.loading}
            emptyTitle={assistant.stringsOf(bundle).empty?.title ?? ""}
            emptyBody={assistant.emptyBody(seeded, bundle)}
            knowsCatalog={knowsTheCatalog(bundle)}
            canConfigure={me.role !== "cashier"}
            transcriptRef={transcriptField}
            onTranscript={setTranscript}
            onCommit={commitTranscript}
            onRemoveChip={removeChip}
            onAdd={(card) => void addSuggestion(card)}
            onConfigure={() => settingsDialog.show("assistant")}
          />
        ) : (
          <SearchColumn
            term={term}
            hits={visible}
            referenceCount={referenceCount}
            locationName={device.location_name}
            onAdd={(hit) => void add(hit.item, 1)}
            onClear={() => {
              setTerm("");
              setUnresolved(null);
              focusCapture();
            }}
            field={captureField}
          />
        )}

        <TicketPanel
          lines={ticketLines}
          average={average}
          locationName={device.location_name}
          canPrice={canPrice}
          busy={busy}
          loading={loading}
          onQuantity={(line, quantity) => {
            void till
              .setQuantity(database, line, quantity)
              .then(refreshTicket)
              .then(focusCapture);
          }}
          onRemove={(line) => {
            void till
              .removeLine(database, line)
              .then(refreshTicket)
              .then(focusCapture);
          }}
          onLot={(entry) => void openLots(entry)}
          onPrice={(entry) => setPricing(entry)}
          onCobrar={() => setCobro(true)}
          onFocusLine={focusCapture}
        />
      </main>

      <OpenShift
        open={!loading && !shift}
        busy={busy}
        failure={failure}
        onOpen={(float) => void begin(float)}
      />

      <Cobro
        open={cobro}
        lines={ticket?.lines ?? []}
        items={items}
        customers={customers}
        attached={attached}
        busy={busy}
        failure={failure}
        onSearchCustomers={(value) => {
          void findCustomers(database, value).then(setCustomers);
        }}
        onAttach={setAttached}
        onRegister={() => setRegistering(true)}
        onCancel={() => {
          setCobro(false);
          focusCapture();
        }}
        onCommit={(applied, change) => void cobrar(applied, change)}
      />

      <Receipt
        open={!!receipt}
        sale={receipt?.sale ?? null}
        lines={receipt?.lines ?? []}
        items={items}
        payments={receipt?.payments ?? []}
        locationName={device.location_name}
        cashierName={me.name}
        change={receipt?.change ?? 0}
        onAgain={() => {
          setReceipt(null);
          focusCapture();
        }}
      />

      <CloseShift
        open={closing}
        shift={shift}
        report={closeReport}
        pending={sync.snapshot?.pending ?? 0}
        busy={busy}
        failure={failure}
        onClose={() => {
          setClosing(false);
          focusCapture();
        }}
        onConfirm={(declared) => void finishClose(declared)}
      />

      <RegisterCustomer
        open={registering}
        onClose={() => {
          setRegistering(false);
          void findCustomers(database, "").then(setCustomers);
        }}
      />

      <FindSale
        open={finding}
        value={findValue}
        notFound={findMissed}
        recent={recent}
        onValue={(next) => {
          setFindValue(next);
          setFindMissed(false);
        }}
        onSubmit={() => void pickByNumber()}
        onPick={(sale) => void openReturn(sale)}
        onClose={() => {
          setFinding(false);
          focusCapture();
        }}
      />

      <LotPicker
        open={!!lots_}
        itemName={lots_?.entry.item?.name ?? "el producto"}
        options={lots_?.options ?? []}
        selected={lots_?.entry.line.lot_id ?? null}
        onPick={(lot, head) => {
          if (!lots_) return;
          void till
            .setLot(database, lots_.entry.line, lot, head)
            .then(refreshTicket)
            .then(() => {
              if (lot) setLots((held) => new Map(held).set(lot.id, lot));
            });
        }}
        onClose={() => {
          setLots_(null);
          focusCapture();
        }}
      />

      <PriceOverride
        open={!!pricing}
        itemName={pricing?.item?.name ?? "el producto"}
        line={pricing?.line ?? null}
        onApply={(values) => {
          if (!pricing) return;
          void till
            .repriceLine(database, pricing.line, values)
            .then(refreshTicket)
            .then(() => {
              setPricing(null);
              focusCapture();
            });
        }}
        onClose={() => {
          setPricing(null);
          focusCapture();
        }}
      />

      <Devolucion
        open={!!returning}
        sale={returning?.ticket.sale ?? null}
        drafts={returning?.drafts ?? []}
        defaultMethod={returning?.method ?? "cash"}
        busy={busy}
        failure={failure}
        foreignLocationName={
          returning && returning.ticket.sale.location_id !== device.location_id
            ? device.location_name
            : null
        }
        onQuantity={(lineId, quantity) =>
          setReturning((held) =>
            held
              ? {
                  ...held,
                  drafts: held.drafts.map((draft) =>
                    draft.line.id === lineId ? { ...draft, quantity } : draft,
                  ),
                }
              : held,
          )
        }
        onClose={() => {
          setReturning(null);
          focusCapture();
        }}
        onConfirm={(values) => void finishReturn(values)}
        onVoid={
          returning?.voidable ? (reason) => void voidTicket(reason) : undefined
        }
      />
    </>
  );

  async function openLots(entry: TicketLine) {
    if (!entry.line.item_id) return;
    setLots_({
      entry,
      options: await till.lotQueue(database!, device!, entry.line.item_id),
    });
  }

  async function begin(openingFloat: number) {
    setBusy(true);
    try {
      const opened = await till.startShift(
        database!,
        device!,
        { id: me.id, name: me.name },
        openingFloat,
      );
      setShift(opened);
      setFailure("");
      focusCapture();
    } catch (error) {
      setFailure(message(error));
    } finally {
      setBusy(false);
    }
  }

  async function beginClose() {
    if (!shift) return;
    const expected = await till.expectedCash(database!, shift);
    const sales = await till.salesOfShift(database!, shift.id);
    const payments = await till.componentsOf(
      database!,
      "payment",
      sales.filter((one) => one.status === "closed").map((one) => one.id),
    );
    const cashSales = payments
      .filter((one) => one.method === "cash")
      .reduce((sum, one) => sum + toCents(one.amount), 0);
    setCloseReport({
      openingFloat: toCents(shift.opening_float),
      cashSales,
      cashReturns: toCents(shift.opening_float) + cashSales - expected,
      expected,
    });
    setClosing(true);
  }

  async function finishClose(declared: number) {
    if (!shift) return;
    setBusy(true);
    try {
      await till.closeShift(database!, shift, declared);
      setClosing(false);
      setShift(null);
      setFailure("");
    } catch (error) {
      setFailure(message(error));
    } finally {
      setBusy(false);
    }
  }

  async function openFinder() {
    const rows = await till.recentSales(database!);
    setRecent(
      rows.map((sale) => ({
        sale,
        label: `${time(sale.occurred_at)} ${DOT} ${sale.sold_by_name}`,
      })),
    );
  }

  async function pickByNumber() {
    const found = await till.findSale(database!, findValue);
    if (!found) {
      setFindMissed(true);
      return;
    }
    await openReturn(found);
  }

  async function openReturn(sale: SaleDoc) {
    const held = await till.readTicket(database!, sale.id);
    if (!held) return;
    const remaining = await till.returnable(database!, held);
    const payments = await till.componentsOf(database!, "payment", [sale.id]);
    const seen = new Map(items);
    for (const line of held.lines) {
      if (!line.item_id || seen.has(line.item_id)) continue;
      const item = (await database!.collections
        .items!.findOne(line.item_id)
        .exec()) as unknown as ItemDoc | null;
      if (item) seen.set(item.id, item);
    }
    setItems(seen);
    setReturning({
      voidable: await till.voidableHere(database!, held, shift),
      ticket: held,
      drafts: held.lines.map((line) => ({
        line,
        item: seen.get(line.item_id ?? ""),
        remaining: remaining[line.id] ?? 0,
        quantity: 0,
      })),
      // The refund defaults to the method the sale was paid with.
      method: payments[0]?.method ?? "cash",
    });
    setFinding(false);
  }

  async function voidTicket(reason: string) {
    if (!returning) return;
    setBusy(true);
    try {
      await till.voidSale(database!, returning.ticket.sale, reason);
      setReturning(null);
      setFailure("");
      focusCapture();
    } catch (error) {
      setFailure(message(error));
    } finally {
      setBusy(false);
    }
  }

  async function finishReturn(values: {
    reason: string;
    refundMethod: string;
  }) {
    if (!returning || !shift) return;
    setBusy(true);
    try {
      await till.registerReturn(
        database!,
        device!,
        shift,
        { id: me.id, name: me.name },
        returning.ticket,
        returning.drafts
          .filter((draft) => draft.quantity > 0)
          .map((draft) => ({
            lineId: draft.line.id,
            quantity: draft.quantity,
          })),
        values,
      );
      setReturning(null);
      setFailure("");
      focusCapture();
    } catch (error) {
      setFailure(message(error));
    } finally {
      setBusy(false);
    }
  }
}

function message(error: unknown) {
  return error instanceof Error
    ? error.message
    : "El navegador rechazó la escritura local.";
}

/** The sede holding the most units, ties broken by name — the same answer the
 *  server's own availability clause gives, so the two never disagree. */
function bestElsewhere(perSede: Map<string, number> | undefined) {
  if (!perSede) return null;
  const ranked = [...perSede.entries()]
    .filter(([name, quantity]) => name && quantity > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const top = ranked[0];
  return top ? { locationName: top[0], quantity: top[1] } : null;
}

/** The products and lots a ticket's own lines name, for a ticket nobody in this
 *  session added a line to. */
async function namesFor(database: SyncDatabase, ticket: till.Ticket) {
  const items = new Map<string, ItemDoc>();
  const lots = new Map<string, LotDoc>();
  const itemIds = [
    ...new Set(ticket.lines.map((line) => line.item_id).filter(Boolean)),
  ] as string[];
  const lotIds = [
    ...new Set(ticket.lines.map((line) => line.lot_id).filter(Boolean)),
  ] as string[];
  if (itemIds.length) {
    const rows = (await database.collections
      .items!.find({ selector: { id: { $in: itemIds } } })
      .exec()) as unknown as { toJSON: () => ItemDoc }[];
    for (const row of rows) {
      const item = row.toJSON();
      items.set(item.id, item);
    }
  }
  if (lotIds.length) {
    const rows = (await database.collections
      .lots!.find({ selector: { id: { $in: lotIds } } })
      .exec()) as unknown as { toJSON: () => LotDoc }[];
    for (const row of rows) {
      const lot = row.toJSON();
      lots.set(lot.id, lot);
    }
  }
  return { items, lots };
}

/**
 * One keystroke's worth of work: the local catalog match, then this sede's
 * stock and thresholds for the handful of rows that came back.
 *
 * Every query is indexed and local, which is what keeps the whole path under
 * §4's 30 ms — and there is no request anywhere on it, which is the half of
 * acceptance 1 that a timing measurement cannot show.
 */
async function resolveHits(
  database: SyncDatabase,
  locationId: string,
  term: string,
): Promise<Hit[]> {
  const found = await searchCatalog(database, term, 25);
  if (found.length === 0) return [];
  const ids = found.map((one) => one.id);
  const items = (await database.collections
    .items!.find({ selector: { id: { $in: ids } } })
    .exec()) as unknown as ItemDoc[];
  const byId = new Map(items.map((item) => [item.id, item]));
  const stock = (await database.collections
    .stock_on_hand!.find({ selector: { item_id: { $in: ids } } })
    .exec()) as unknown as {
    item_id: string;
    location_id: string;
    quantity: number;
    location_name: string | null;
  }[];
  const held = new Map<string, number>();
  // **The other-location set S3 already pulls onto the device**, which is what
  // `hay 96 en Suba` is rendered from. It shares this store with the sede's own
  // stock and is split by the `location_id` every row carries.
  const away = new Map<string, Map<string, number>>();
  for (const row of stock) {
    if (row.location_id === locationId) {
      held.set(row.item_id, (held.get(row.item_id) ?? 0) + row.quantity);
      continue;
    }
    const perSede = away.get(row.item_id) ?? new Map<string, number>();
    perSede.set(
      row.location_name ?? "",
      (perSede.get(row.location_name ?? "") ?? 0) + row.quantity,
    );
    away.set(row.item_id, perSede);
  }
  const policies = (await database.collections
    .stock_policies!.find({
      selector: { kind: "policy", item_id: { $in: ids } },
    })
    .exec()) as unknown as PolicyDoc[];
  const reorder = new Map<string, number | null>();
  for (const row of policies) {
    // A sede's own row wins over the network-wide one, whole — the same
    // precedence the server's `Estado` derivation applies.
    if (row.location_id === null && reorder.has(row.item_id)) continue;
    reorder.set(row.item_id, row.reorder_point);
  }
  const manufacturers = (await database.collections
    .manufacturers!.find()
    .exec()) as unknown as { id: string; name: string }[];
  const labs = new Map(manufacturers.map((one) => [one.id, one.name]));

  return found
    .map((one) => {
      const item = byId.get(one.id);
      if (!item) return null;
      return {
        item,
        price: one.price === null ? null : toCents(one.price),
        quantity: held.get(one.id) ?? 0,
        reorderPoint: reorder.get(one.id) ?? null,
        manufacturer: item.manufacturer_id
          ? (labs.get(item.manufacturer_id) ?? "")
          : "",
        // Shown only when this sede is out: the clause exists to tell a cashier
        // holding a customer where the box is, and a second figure beside a
        // healthy one is noise.
        elsewhere:
          (held.get(one.id) ?? 0) > 0 ? null : bestElsewhere(away.get(one.id)),
      } satisfies Hit;
    })
    .filter((one): one is Hit => one !== null);
}

export type { CustomerPick };
