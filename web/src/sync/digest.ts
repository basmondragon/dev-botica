import type { CollectionName } from "./registry";
import type { SyncDatabase } from "./store";

/**
 * The local half of the daily divergence check.
 *
 * **It hashes exactly what the server would have sent**, in exactly the same
 * order and the same string form: `${id}:${updated_at}\n` per document, ordered
 * by `(updated_at, id)`. The server computes its side in Python over the same
 * strings its pull serialises, for this reason — a SQL `md5(string_agg(...))`
 * would compare a timestamp Postgres formatted against one the browser stored,
 * which is a permanent false mismatch that re-pulls every collection every day.
 */

export interface LocalDigest {
  count: number;
  checksum: string;
}

export async function localDigest(
  database: SyncDatabase,
  name: CollectionName,
): Promise<LocalDigest> {
  const rows = (await database.collections[name]!.find().exec()) as unknown as {
    id: string;
    updated_at: string;
  }[];
  const ordered = rows
    .map((row) => ({ id: row.id, updated_at: row.updated_at }))
    .sort((a, b) =>
      a.updated_at === b.updated_at
        ? a.id.localeCompare(b.id)
        : a.updated_at.localeCompare(b.updated_at),
    );
  const body = ordered.map((row) => `${row.id}:${row.updated_at}\n`).join("");
  return { count: ordered.length, checksum: await sha256(body) };
}

async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
