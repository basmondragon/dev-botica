import { forwardRef, useEffect, useState } from "react";
import { Sparkles, X } from "lucide-react";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { CONTROL_BASE } from "@/ui/field";
import { DOT, money as pesosOf } from "@/ui/format";
import { SkeletonBar } from "@/ui/states";
import { StatusDot, TypePill } from "@/ui/status";
import { pesos } from "@/counter/money";
import type { Fact } from "./extract";
import type { Card } from "./pipeline";
import {
  ADD,
  ADDED,
  CONFIGURE_ACTION,
  CONFIGURE_BODY,
  CONFIGURE_TITLE,
  EYEBROW_LOCAL,
  EYEBROW_SUGGESTIONS,
  EYEBROW_TRANSCRIPT,
  PLACEHOLDER,
  SLOW,
  SLOW_AFTER_MS,
  TYPE_FAMILY,
  TYPE_LABELS,
  counterLabel,
  unitsLabel,
} from "./vocabulary";

/**
 * `Mostrador` · the assistant column — a **counter surface** at counter density
 * (§B.11): 44px controls, `t-16` recommendation, `t-14` context lines, a 44px
 * minimum hit target, no single-letter shortcuts, and focus returning to S4's
 * capture field after every action (§B.13.3).
 *
 * **There is no error treatment on this surface.** Every failure resolves to
 * local mode: §B.10.3 is binding that no error at the counter obstructs a sale,
 * and there is nothing here a cashier could retry that the fallback has not
 * already done.
 */

// ---------------------------------------------------------------------------
// Card A · what the customer says
// ---------------------------------------------------------------------------

export const TranscriptCard = forwardRef<
  HTMLTextAreaElement,
  {
    value: string;
    facts: Fact[];
    onChange: (next: string) => void;
    onCommit: () => void;
    onRemoveChip: (fact: Fact) => void;
  }
>(function TranscriptCard(
  { value, facts, onChange, onCommit, onRemoveChip },
  ref,
) {
  const chips = facts.filter((fact) => fact.kind !== "duration");
  return (
    <Section eyebrow={EYEBROW_TRANSCRIPT}>
      <textarea
        ref={ref}
        rows={2}
        value={value}
        spellCheck
        aria-label={EYEBROW_TRANSCRIPT}
        placeholder={PLACEHOLDER}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          // `Enter` commits and `Shift+Enter` does not, because a cashier
          // dictating a long sentence needs a line break and a cashier typing
          // one needs the pipeline to run.
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            onCommit();
          }
        }}
        className={cn(
          CONTROL_BASE,
          "w-full resize-none bg-canvas px-3.5 py-3 text-14",
        )}
      />
      {chips.length > 0 ? (
        <ul className="mt-3 flex flex-wrap gap-2">
          {chips.map((fact) => (
            <li key={`${fact.kind}:${fact.key}`}>
              <Chip fact={fact} onRemove={() => onRemoveChip(fact)} />
            </li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
});

/**
 * **A chip is not a display artefact, it is the filter's input.** Removing one
 * re-runs the whole pipeline including the filter, because a screen where
 * deleting *fiebre* leaves loperamida still filtered is a screen that has
 * stopped telling the truth.
 */
function Chip({ fact, onRemove }: { fact: Fact; onRemove: () => void }) {
  const treatment = fact.kind === "active_treatment";
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1.5 rounded-pill pl-2.5 pr-1.5 text-11",
        treatment ? "bg-tint-info text-ink" : "bg-active text-ink-body",
      )}
    >
      {fact.label}
      <button
        type="button"
        aria-label={`Quitar ${fact.label}`}
        onClick={onRemove}
        className="inline-flex size-4 items-center justify-center rounded-pill text-ink-note transition-colors duration-140 ease-out hover:text-ink"
      >
        <X aria-hidden className="size-3" strokeWidth={1.8} />
      </button>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Card B · the recommendation
// ---------------------------------------------------------------------------

/**
 * §A.19.5 · the assistant card: no header, `padding:20px`, a 26×26 tile on the
 * brand blue holding the sparkle. **This tile is the one place blue is an
 * identity rather than a quantity** (§B.12.1).
 *
 * Only this card has a genuinely async state. The local pipeline resolves in
 * well under the budget, so cards A and C render populated immediately.
 */
export function RecommendationCard({
  primary,
  secondary,
  local,
  loading,
}: {
  primary: string;
  secondary: string;
  local: boolean;
  loading: boolean;
}) {
  const slow = useSlow(loading);
  return (
    <section className="relative flex shrink-0 gap-3.5 rounded-panel border border-edge-soft bg-surface p-5 shadow-plane">
      <span
        aria-hidden
        className="mt-0.5 inline-flex size-[26px] shrink-0 items-center justify-center rounded-icon bg-brand"
      >
        <Sparkles className="size-[15px] text-surface" strokeWidth={1.8} />
      </span>
      <div className="min-w-0 flex-1">
        {loading ? (
          <>
            {/* §B.10.1 · a geometry-matched skeleton at the real 14/20 and
                12/18 leading — never a spinner, and the blue tile paints at
                once beside it. */}
            <SkeletonBar className="h-3.5 w-4/5" />
            <SkeletonBar className="mt-2.5 h-3 w-3/5" />
            {slow ? <p className="mt-2 text-12 text-ink-note">{SLOW}</p> : null}
          </>
        ) : (
          <>
            <p className="text-16 text-ink">{primary}</p>
            {secondary ? (
              <p className="mt-2 text-12 text-ink-body">{secondary}</p>
            ) : null}
          </>
        )}
      </div>
      {local ? (
        <span className="absolute right-4 top-4 font-mono text-10 uppercase tracking-eyebrow text-ink-note">
          {EYEBROW_LOCAL}
        </span>
      ) : null}
    </section>
  );
}

/** §B.10.1 · the line under the skeleton at 2,5 s. It is a **timer and not a
 *  state machine**: the card resolves to the local recommendation on its own,
 *  so the only thing this decides is whether one sentence is on screen while it
 *  waits. */
function useSlow(loading: boolean) {
  const [slow, setSlow] = useState(false);
  const [previous, setPrevious] = useState(loading);
  // React's own "adjust state when a prop changes" pattern: a second question
  // starts its own 2,5 s, rather than inheriting the first one's line.
  if (previous !== loading) {
    setPrevious(loading);
    setSlow(false);
  }
  useEffect(() => {
    if (!loading) return;
    const timer = window.setTimeout(() => setSlow(true), SLOW_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [loading]);
  return loading && slow;
}

// ---------------------------------------------------------------------------
// Card C · the suggestions, and the notice that ships inside it
// ---------------------------------------------------------------------------

export function SuggestionsCard({
  cards,
  surviving,
  locationName,
  added,
  emptyTitle,
  emptyBody,
  knowsCatalog,
  canConfigure,
  onAdd,
  onConfigure,
}: {
  cards: Card[];
  surviving: number;
  locationName: string;
  added: ReadonlySet<string>;
  emptyTitle: string;
  emptyBody: string;
  knowsCatalog: boolean;
  canConfigure: boolean;
  onAdd: (card: Card) => void;
  onConfigure: () => void;
}) {
  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-panel border border-edge-soft bg-surface shadow-plane">
      <header className="flex h-10 shrink-0 items-center gap-4 border-b border-hairline bg-chrome px-5">
        <h2 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
          {EYEBROW_SUGGESTIONS}
        </h2>
        {knowsCatalog ? (
          <span className="ml-auto text-11 tabular-nums text-ink-note">
            {counterLabel(cards.length, surviving)}
          </span>
        ) : null}
      </header>

      <div className="flex min-h-0 flex-1 flex-col px-5 py-4">
        {!knowsCatalog ? (
          /* §B.10.2 · a section a capability can empty is gated at its header.
             **This is configuration and not the cold-start floor**: a demo that
             opened here and read it as day one would conclude the assistant
             needs a data migration, which is the precise claim §1 denies. */
          <div className="mx-auto flex max-w-[420px] flex-col items-center py-8 text-center">
            <p className="text-16 text-ink">{CONFIGURE_TITLE}</p>
            <p className="mt-2 text-14 text-ink-body">{CONFIGURE_BODY}</p>
            {canConfigure ? (
              <Button
                size="md"
                variant="primary"
                className="mt-5 h-control-counter"
                onClick={onConfigure}
              >
                {CONFIGURE_ACTION}
              </Button>
            ) : null}
          </div>
        ) : cards.length === 0 ? (
          /* §B.10.2 · the deliberately-empty shape, and it carries **no
             action**: this is a statement about the shelf, not something a
             cashier can fix from here. */
          <div className="mx-auto flex max-w-[420px] flex-col items-center py-8 text-center">
            <p className="text-16 text-ink">{emptyTitle}</p>
            <p className="mt-2 text-14 text-ink-body">{emptyBody}</p>
          </div>
        ) : (
          /* At the 1280×720 counter floor the list scrolls **inside its own
             region** and the notice below stays put: a mandatory notice that
             can be scrolled out of view is a notice that is not mandatory on a
             short screen. */
          <ul className="-mx-1 min-h-0 flex-1 space-y-2.5 overflow-y-auto px-1">
            {cards.map((card, index) => (
              <li key={card.item.id}>
                <Suggestion
                  card={card}
                  first={index === 0}
                  locationName={locationName}
                  added={added.has(card.item.id)}
                  onAdd={() => onAdd(card)}
                />
              </li>
            ))}
          </ul>
        )}

        {/*
          **The advisory notice.** It ships inside this component and there is
          no prop, setting, role or feature flag that removes it (A8, §A.19.3,
          handoff: *"Este aviso es obligatorio y no debe eliminarse"*). It
          renders on every state of this card — three suggestions, one, none, in
          local mode and offline — because a card C with no products still
          carries the sentence about not diagnosing.
        */}
        <p className="mt-auto flex shrink-0 items-start gap-2.5 border-t border-hairline pt-3 text-12/[18px] text-ink-body">
          <span className="mt-1">
            <StatusDot family="warning" />
          </span>
          Con fiebre de más de dos días, remitir a consulta médica. Botica no
          diagnostica.
        </p>
      </div>
    </section>
  );
}

/**
 * §A.19.3 · one suggestion. The context line is always **units at this sede**
 * then **the reason**, and the whole line is `t-14` at counter density.
 *
 * The card **stays on screen** with its button in the pressed-and-added state:
 * a card that vanishes on click is a card whose price the cashier can no longer
 * read out.
 */
function Suggestion({
  card,
  first,
  locationName,
  added,
  onAdd,
}: {
  card: Card;
  first: boolean;
  locationName: string;
  added: boolean;
  onAdd: () => void;
}) {
  return (
    <div className="flex items-center gap-4 rounded-card border border-hairline px-4 py-3.5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <p className="truncate text-14 text-ink">{card.item.name}</p>
          <TypePill family={TYPE_FAMILY[card.type]}>
            {TYPE_LABELS[card.type]}
          </TypePill>
        </div>
        <p className="mt-1 text-14 text-ink-note">
          {unitsLabel(card.availableQuantity, locationName)} {DOT} {card.reason}
        </p>
      </div>
      <span className="shrink-0 whitespace-nowrap text-14 tabular-nums text-ink">
        {pesosOf(pesos(card.price))}
      </span>
      <Button
        size="md"
        variant={first && !added ? "primary" : "secondary"}
        className="h-control-counter shrink-0"
        disabled={added}
        onClick={onAdd}
      >
        {added ? ADDED : ADD}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The shared section shell
// ---------------------------------------------------------------------------

function Section({
  eyebrow,
  children,
}: {
  eyebrow: string;
  children: React.ReactNode;
}) {
  return (
    <section className="shrink-0 overflow-hidden rounded-panel border border-edge-soft bg-surface shadow-plane">
      <header className="flex h-10 items-center border-b border-hairline bg-chrome px-5">
        <h2 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
          {eyebrow}
        </h2>
      </header>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}
