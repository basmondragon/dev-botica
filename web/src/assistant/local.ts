import type { SyncDatabase } from "@/sync/store";
import type { SaleLineDoc } from "@/sync/registry";
import { queue, uuidV7 } from "@/sync/outbox";
import { fromCents } from "@/counter/money";
import type { Fact } from "./extract";
import type { Card } from "./pipeline";

/**
 * What the till writes when it draws a card, and what it writes when one is
 * taken.
 *
 * **The offer is recorded when it is shown, not when it is accepted**, which is
 * the whole of why the acceptance rate has a denominator. Both writes go
 * through S2's outbox — one path online and offline — and the online call to
 * `POST /api/assistant/queries` is for the recommendation's prose, not for the
 * record: the row it would create is idempotent on the same `client_uuid`, so
 * whichever arrives first wins and the other is a `duplicate`, which is a
 * success (A5).
 *
 * **The order of the three events is the correctness.** `client_uuid` is uuid
 * v7 and the push applies a batch in that order, so:
 *
 *   at the offer     the query, carrying its cards
 *   at the close     S4's lines and its sale, then the `attach` that names the
 *                    ticket, then one acceptance per card that was taken
 *
 * An acceptance minted before the line it credits would arrive before it, and
 * *a batch that applies the line and not the flag is a batch that under-reports
 * the assistant forever*.
 */

export interface Offer {
  /** The query's own id, which is also its `client_uuid`. */
  id: string;
  cards: { id: string; card: Card }[];
}

const ACCEPTED_KEY = "botica.assistant.accepted";
const ASKED_KEY = "botica.assistant.asked";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Mint the ids the offer and its cards will carry, before anything is queued,
 *  so the screen and the record name the same rows. */
export function mintOffer(cards: Card[]): Offer {
  return {
    id: uuidV7(),
    cards: cards.map((card) => ({ id: uuidV7(), card })),
  };
}

export function offerPayload(
  offer: Offer,
  {
    facts,
    transcript,
    candidateCount,
    excluded,
    bundleVersion,
    saleId,
    userId,
    userName,
    recommendation,
    recommendationSecondary,
    retainTranscript,
  }: {
    facts: Fact[];
    transcript: string;
    candidateCount: number;
    excluded: unknown[];
    bundleVersion: string;
    saleId: string | null;
    userId: string;
    userName: string;
    recommendation: string;
    recommendationSecondary: string;
    retainTranscript: boolean;
  },
) {
  return {
    client_uuid: offer.id,
    // **Health data, and the till decides** (§11.3). Where retention is off the
    // transcript never leaves this browser at all: the extracted keys and
    // labels go, and the words do not.
    transcript: retainTranscript ? transcript : "",
    symptoms: facts,
    candidates: offer.cards.map(({ id, card }, index) => ({
      client_uuid: id,
      item_id: card.item.id,
      type: card.type,
      reason: card.reason,
      reason_code: card.reasonCode,
      price: fromCents(card.price),
      rank: index + 1,
      available_quantity: card.availableQuantity,
      warning_id: card.warningId,
      rule_confidence: card.ruleConfidence,
    })),
    candidate_count: candidateCount,
    excluded,
    bundle_version: bundleVersion,
    sale_client_uuid: saleId,
    user_id: userId,
    // **The name, stamped rather than joined**: §2 hard-deletes a `users` row
    // and `Registro del asistente` has to keep saying who asked.
    user_name: userName,
    recommendation,
    recommendation_secondary: recommendationSecondary,
    event: "offer",
  };
}

export async function queueOffer(
  database: SyncDatabase,
  payload: Record<string, unknown>,
) {
  await queue(database, "assistant_queries", payload);
}

/**
 * Every query asked while this ticket was being built, held until it closes.
 *
 * **All of them, not only the ones a card was taken from.** The acceptance rate
 * counts offers on queries attached to a closed counter sale, so a query whose
 * cards nobody took has to reach the same population — otherwise the
 * denominator is the numerator and the tile reads 100% forever. A question
 * asked and not acted on is exactly the case the rate exists to measure.
 *
 * It is minted before any ticket exists (the cashier asks, then scans), so it
 * is keyed on nothing and cleared at close: the questions asked while this
 * ticket was open belong to this ticket, which is what a counter means by it.
 */
export function rememberQuery(queryId: string) {
  const held = readAsked();
  if (held.includes(queryId)) return;
  storage()?.setItem(ASKED_KEY, JSON.stringify([...held, queryId]));
}

export function readAsked(): string[] {
  const raw = storage()?.getItem(ASKED_KEY);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as string[]) : [];
  } catch {
    return [];
  }
}

export function forgetAsked() {
  storage()?.removeItem(ASKED_KEY);
}

/**
 * Which card became which line, held until the ticket closes.
 *
 * **In `localStorage` and not in React state**, because a ticket survives a
 * relaunch and is shared by a second tab: a mapping that lived only in the
 * component would leave `from_suggestion` true on a line no suggestion is
 * credited for, and the two counts check 10 compares would stop being equal.
 */
export interface Credit {
  suggestion_id: string;
  query_id: string;
  line_id: string;
}

export function rememberCredit(saleId: string, credit: Credit) {
  const held = readCredits(saleId).filter(
    (one) => one.suggestion_id !== credit.suggestion_id,
  );
  storage()?.setItem(
    `${ACCEPTED_KEY}.${saleId}`,
    JSON.stringify([...held, credit]),
  );
}

export function readCredits(saleId: string): Credit[] {
  const raw = storage()?.getItem(`${ACCEPTED_KEY}.${saleId}`);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Credit[]) : [];
  } catch {
    return [];
  }
}

export function forgetCredits(saleId: string) {
  storage()?.removeItem(`${ACCEPTED_KEY}.${saleId}`);
}

/**
 * The two events a closing ticket owes the assistant, **queued after S4's own**.
 *
 * `attach` names the sale the question was asked against — a second event and
 * not a field on the first, because the sale does not exist on the server when
 * the offer is recorded. The acceptances follow it, each naming the line it
 * credits by the id S4's writer keeps.
 */
export async function queueClose(
  database: SyncDatabase,
  saleId: string,
  lines: SaleLineDoc[],
) {
  const credits = readCredits(saleId);
  const asked = [
    ...new Set([...readAsked(), ...credits.map((one) => one.query_id)]),
  ];
  const present = new Set(lines.map((line) => line.id));
  for (const queryId of asked) {
    await queue(database, "assistant_queries", {
      client_uuid: queryId,
      sale_client_uuid: saleId,
      event: "attach",
    });
  }
  for (const credit of credits) {
    if (!present.has(credit.line_id)) continue;
    await queue(database, "assistant_suggestions", {
      client_uuid: credit.suggestion_id,
      sale_line_id: credit.line_id,
      event: "accept",
    });
  }
  forgetCredits(saleId);
  forgetAsked();
}

/** The cashier re-asked on the same open sale: the previous query's
 *  un-accepted suggestions leave the denominator. */
export async function queueSupersede(database: SyncDatabase, queryId: string) {
  await queue(database, "assistant_queries", {
    client_uuid: queryId,
    event: "supersede",
  });
}
