import type { components } from "@/api/schema.gen";

/**
 * The device record this browser holds: its id, its key and where it is.
 *
 * **It lives in `localStorage` and not in the local store**, because it has to
 * be readable before the store exists — the store's name is derived from the
 * device, and the first thing the application does on a till is decide whether
 * this browser is a device at all.
 *
 * The `device_key` is returned by the server exactly once, at claim, and is
 * stored hashed at rest there. This is the only copy. Losing it costs one
 * re-claim and one first sync and **no data**: the outbox is drained before a
 * re-claim is offered.
 */

const KEY = "botica.device";

export type ClaimResponse = components["schemas"]["ClaimOut"];

export interface DeviceRecord {
  id: string;
  key: string;
  label: string;
  location_id: string;
  location_name: string;
  location_code: string;
  /** Whether the browser granted persistent storage, as last answered. `null`
   *  is **not yet reported** and is never `false`. */
  persisted: boolean | null;
  /** Whether the one-time persistence dialog has been shown. §B.9.4 says once,
   *  and a dialog an operator dismisses every morning is a dialog they click
   *  through without reading. */
  persistence_dialog_seen: boolean;
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    // A browser with site data blocked outright. It cannot be a device, and the
    // claim card is what says so.
    return null;
  }
}

export function readDevice(): DeviceRecord | null {
  const raw = storage()?.getItem(KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof (parsed as DeviceRecord).id === "string" &&
      typeof (parsed as DeviceRecord).key === "string"
    ) {
      return parsed as DeviceRecord;
    }
  } catch {
    // A record we cannot read is a record we do not have. The claim card is
    // the honest answer, and it costs one first sync.
  }
  return null;
}

export function writeDevice(record: DeviceRecord) {
  storage()?.setItem(KEY, JSON.stringify(record));
  return record;
}

export function forgetDevice() {
  storage()?.removeItem(KEY);
}

export function recordFromClaim(claim: ClaimResponse): DeviceRecord {
  return {
    id: claim.device.id,
    key: claim.device_key,
    label: claim.device.label,
    location_id: claim.device.location_id,
    location_name: claim.device.location_name,
    location_code: "",
    persisted: claim.device.storage_persisted ?? null,
    persistence_dialog_seen: false,
  };
}

/**
 * §B.9.4 · `navigator.storage.persist()` is requested at claim and **its state
 * is displayed**. An unsynced sale living in evictable storage is a risk the
 * operator must be told about, and it is the only technical browser detail this
 * product ever puts in front of a cashier.
 *
 * A refusal is `degraded`, never `blocked` (§5, §B.9.4): a till that refuses to
 * sell over a risk of eviction trades a possible loss for a certain one.
 */
export async function requestPersistence(): Promise<boolean | null> {
  if (typeof navigator === "undefined" || !navigator.storage) return null;
  try {
    if (await navigator.storage.persisted?.()) return true;
    if (!navigator.storage.persist) return null;
    return await navigator.storage.persist();
  } catch {
    return null;
  }
}

/** What §B.9.3 renders as `Espacio usado · 9,4 MB`, read from the browser
 *  rather than calculated — which is what criterion 4 measures. */
export async function storageUsedBytes(): Promise<number | null> {
  if (typeof navigator === "undefined" || !navigator.storage?.estimate)
    return null;
  try {
    const estimate = await navigator.storage.estimate();
    return estimate.usage ?? null;
  } catch {
    return null;
  }
}
