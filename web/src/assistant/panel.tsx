import type { ReactNode } from "react";
import { cn } from "@/ui/cn";
import type { Fact } from "./extract";
import type { Card } from "./pipeline";
import { RecommendationCard, SuggestionsCard, TranscriptCard } from "./column";

/**
 * The left column of S4's Mostrador once this stage takes it: `flex:1`,
 * `gap:16px`, three cards top to bottom (handoff, §A.22).
 *
 * **S4's capture field stays where it was, above them.** The column this stage
 * borrows is S4's, and the field at the top of it is what a barcode burst lands
 * in — moving it would break the one path §4 budgets at 30 ms and criterion 27
 * measures. What moves is the *results list*, which re-renders as an L3 overlay
 * anchored under the field exactly as S4's own component was built to do.
 *
 * **Setting `enabled` to false does not render this at all**: S4's
 * `BUSCAR PRODUCTO` list returns to the full-height left column it held before,
 * the 420px ticket panel keeps its width, and there is no disabled state,
 * because a greyed assistant teaches nothing (§B.8.3).
 */
export function AssistantColumn({
  field,
  overlay,
  asked,
  transcript,
  facts,
  cards,
  surviving,
  locationName,
  added,
  primary,
  secondary,
  local,
  loading,
  emptyTitle,
  emptyBody,
  knowsCatalog,
  canConfigure,
  transcriptRef,
  onTranscript,
  onCommit,
  onRemoveChip,
  onAdd,
  onConfigure,
}: {
  field: ReactNode;
  overlay: ReactNode;
  /** Whether a transcript has been committed at all. **Idle is card A alone**:
   *  a section a capability can empty is gated at its header, not left as a
   *  gap (§B.10.2). */
  asked: boolean;
  transcript: string;
  facts: Fact[];
  cards: Card[];
  surviving: number;
  locationName: string;
  added: ReadonlySet<string>;
  primary: string;
  secondary: string;
  local: boolean;
  loading: boolean;
  emptyTitle: string;
  emptyBody: string;
  knowsCatalog: boolean;
  canConfigure: boolean;
  transcriptRef: React.RefObject<HTMLTextAreaElement | null>;
  onTranscript: (next: string) => void;
  onCommit: () => void;
  onRemoveChip: (fact: Fact) => void;
  onAdd: (card: Card) => void;
  onConfigure: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="relative shrink-0">
        <div className="overflow-hidden rounded-panel border border-edge-soft bg-surface shadow-plane">
          <div className="px-5 py-4">{field}</div>
        </div>
        {overlay ? (
          <div
            className={cn(
              "absolute inset-x-0 top-full z-30 mt-2 flex max-h-[420px] flex-col",
              "overflow-hidden rounded-panel border border-edge-soft bg-surface shadow-overlay",
            )}
          >
            {overlay}
          </div>
        ) : null}
      </div>

      <TranscriptCard
        ref={transcriptRef}
        value={transcript}
        facts={facts}
        onChange={onTranscript}
        onCommit={onCommit}
        onRemoveChip={onRemoveChip}
      />

      {asked ? (
        <RecommendationCard
          primary={primary}
          secondary={secondary}
          local={local}
          loading={loading}
        />
      ) : null}

      {asked ? (
        <SuggestionsCard
          cards={cards}
          surviving={surviving}
          locationName={locationName}
          added={added}
          emptyTitle={emptyTitle}
          emptyBody={emptyBody}
          knowsCatalog={knowsCatalog}
          canConfigure={canConfigure}
          onAdd={onAdd}
          onConfigure={onConfigure}
        />
      ) : null}
    </div>
  );
}
