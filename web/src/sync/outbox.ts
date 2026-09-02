import type { SyncDatabase } from "./store";
import type { OutboxDoc } from "./registry";
import type { DeviceRecord } from "./device";
import { postPush, ServerRefused, Unreachable } from "./transport";

/**
 * The outbox and its drain.
 *
 * **Every outcome except a transport failure removes the row.** `applied`,
 * `duplicate` and `merged` are all successes; `rejected` is a data outcome and
 * the row leaves too, because a row retried forever is a `degraded` state that
 * never clears. A client that treats `duplicate` as an error can never drain a
 * queue whose push timed out after the server committed, which is the exact
 * failure A5 exists to make safe.
 */

export interface DrainReport {
  applied: number;
  duplicate: number;
  merged: number;
  rejected: number;
  /** `merged` returns the server's id, and the local row adopts it silently:
   *  the cashier registered a person who was already known, which is not an
   *  event worth telling them about. */
  merges: { collection: string; from: string; to: string }[];
  /** Whether the whole batch was refused for a foreign tenant or location. */
  batchRejected: boolean;
  reason: string;
}

export const EMPTY: DrainReport = {
  applied: 0,
  duplicate: 0,
  merged: 0,
  rejected: 0,
  merges: [],
  batchRejected: false,
  reason: "",
};

function uuidV7(): string {
  // uuid v7, so `client_uuid` sorts by time and the server can apply a batch in
  // the order the counter produced it (A5).
  const now = BigInt(Date.now());
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  for (let index = 0; index < 6; index += 1) {
    bytes[index] = Number((now >> BigInt(8 * (5 - index))) & 0xffn);
  }
  bytes[6] = (bytes[6]! & 0x0f) | 0x70;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

export { uuidV7 };

/**
 * Queue one client-originated write.
 *
 * The row goes to the local store **and** to the outbox in the same breath and
 * with no loading state at all (§B.10.1, optimistic writes). The status line
 * moves to `Sin conexión · 1 por enviar`, and the customer is findable by name
 * and document immediately — which is criterion 8.
 */
export async function queue(
  database: SyncDatabase,
  kind: string,
  payload: Record<string, unknown>,
): Promise<OutboxDoc> {
  const row: OutboxDoc = {
    client_uuid: uuidV7(),
    kind,
    // The device's own clock. Whatever it says, it is stored as it stands.
    occurred_at: new Date().toISOString(),
    payload,
    attempts: 0,
    last_error: "",
    queued_at: new Date().toISOString(),
  };
  await database.collections.outbox!.insert(row);
  return row;
}

export async function depth(database: SyncDatabase): Promise<number> {
  return database.collections.outbox!.count().exec();
}

/** §B.9.3 · the queue broken down by kind — `Clientes 1` at S2. */
export async function byKind(
  database: SyncDatabase,
): Promise<Record<string, number>> {
  const rows = (await database.collections
    .outbox!.find()
    .exec()) as unknown as OutboxDoc[];
  const counts: Record<string, number> = {};
  for (const row of rows) {
    counts[row.kind] = (counts[row.kind] ?? 0) + 1;
  }
  return counts;
}

/**
 * Send one batch and settle every row it carried.
 *
 * **The push runs before the pull in every cycle.** A till that has something
 * to say says it before it listens, so a sale is on the server at the earliest
 * possible moment and the pull that follows already reflects it.
 */
export async function drain(
  database: SyncDatabase,
  device: DeviceRecord,
  maxRows: number,
): Promise<DrainReport> {
  const outbox = database.collections.outbox!;
  const pending = (await outbox
    .find({ sort: [{ client_uuid: "asc" }], limit: maxRows })
    .exec()) as unknown as OutboxDoc[];
  if (pending.length === 0) return EMPTY;

  const batchId = pending[0]!.client_uuid;
  let response;
  try {
    response = await postPush(device, {
      batch_id: batchId,
      client_time: new Date().toISOString(),
      rows: pending.map((row) => ({
        collection: row.kind,
        client_uuid: row.client_uuid,
        occurred_at: row.occurred_at,
        payload: row.payload,
      })),
    });
  } catch (failure) {
    // A transport failure leaves every row where it is and bumps its attempt
    // count. **Nothing unsent is ever dropped for failing to send.**
    const note =
      failure instanceof ServerRefused ? failure.detail : "sin conexión";
    for (const row of pending) {
      const document = await outbox.findOne(row.client_uuid).exec();
      await document?.incrementalPatch({
        attempts: row.attempts + 1,
        last_error: note,
      });
    }
    throw failure;
  }

  const report: DrainReport = {
    ...EMPTY,
    merges: [],
    batchRejected: response.batch_outcome === "rejected",
    reason: response.batch_reason ?? "",
  };

  /**
   * **A whole-batch rejection removes nothing.**
   *
   * The server applied no row in it, so removing them would delete writes that
   * were never made — and a foreign-tenant or foreign-location refusal means
   * this till is wrong about which network or sede it is at, which is not a
   * thing a cashier can fix and not a thing a retry will change. The rows stay,
   * the till goes `degraded`, and the office sees the conflict and re-claims or
   * revokes the device. A re-claim drains the outbox first, so nothing is lost.
   *
   * This is the same rule a reset follows, arriving by a different door: **a
   * forced wipe with a full outbox loses sales**, and that is the single
   * failure this whole stage exists to prevent.
   */
  if (report.batchRejected) {
    for (const row of pending) {
      const document = await outbox.findOne(row.client_uuid).exec();
      await document?.incrementalPatch({
        attempts: row.attempts + 1,
        last_error: report.reason || "el servidor rechazó el lote completo",
      });
    }
    report.rejected = pending.length;
    return report;
  }

  const byUuid = new Map(pending.map((row) => [row.client_uuid, row]));

  for (const outcome of response.results) {
    const row = byUuid.get(outcome.client_uuid);
    if (!row) continue;
    if (outcome.outcome === "merged" && outcome.id) {
      const localId = String(row.payload.id ?? "");
      if (localId && localId !== outcome.id) {
        report.merges.push({
          collection: row.kind,
          from: localId,
          to: outcome.id,
        });
      }
    }
    report[outcome.outcome] += 1;
    // Every one of the four removes it. Three are successes; the fourth is
    // surfaced through the conflict queue rather than retried forever.
    const document = await outbox.findOne(row.client_uuid).exec();
    await document?.remove();
  }
  return report;
}

export { ServerRefused, Unreachable };
