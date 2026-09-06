import { APP_VERSION } from "@/app-version";
import type { components } from "@/api/schema.gen";
import type { DeviceRecord } from "@/sync/device";

/**
 * The client reference bundle: the lexicon, the closed vocabulary, this
 * tenant's `symptom_category_map`, every Spanish sentence the column composes
 * from, and the two settings the column's own shape depends on.
 *
 * **It is not a registry collection, and that is deliberate** (S8, *Why the
 * reference bundle is not a collection*). It is one document of a few
 * kilobytes with no per-row deltas and no natural key worth versioning, so it
 * is cached with the device record in `localStorage` and refreshed whenever its
 * version changes — the same treatment S2 gives the sede's own name and code. A
 * fifth collection to compact and reconcile for one document is what rule 9
 * exists to prevent.
 *
 * **One copy of every Spanish sentence in the product.** The reason lines, the
 * empty-state copy and card B's local register all come down from the server
 * rather than being written twice, so the offline recommendation and the
 * server's own are the same words. The one string that does **not** come down
 * this wire is the advisory notice: a notice delivered over the wire is a
 * notice a deployment can empty, so it ships inside the component (A8).
 */

export type Bundle = components["schemas"]["AssistantBundleOut"];

const KEY = "botica.assistant.bundle";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readBundle(): Bundle | null {
  const raw = storage()?.getItem(KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && "version" in parsed) {
      return parsed as Bundle;
    }
  } catch {
    // A bundle we cannot read is a bundle we do not have. The column renders
    // its configuration empty state and the next online cycle replaces it.
  }
  return null;
}

export function writeBundle(bundle: Bundle) {
  storage()?.setItem(KEY, JSON.stringify(bundle));
  return bundle;
}

export function forgetBundle() {
  storage()?.removeItem(KEY);
}

/**
 * Fetch it, or keep what we hold.
 *
 * **A failure is not an event.** With no connection the till extracts against
 * the bundle it already has, which is the point of caching it; with no bundle
 * at all the column renders the configuration empty state, which is the same
 * shape as an unpopulated `symptom_category_map` and is the honest reading.
 */
export async function refreshBundle(
  device: DeviceRecord,
): Promise<Bundle | null> {
  try {
    const response = await fetch("/api/assistant/bundle", {
      credentials: "same-origin",
      headers: {
        "X-Botica-Device-Key": device.key,
        "X-Botica-App-Version": APP_VERSION,
      },
    });
    if (!response.ok) return readBundle();
    const fetched = (await response.json()) as Bundle;
    const held = readBundle();
    if (held && held.version === fetched.version) return held;
    return writeBundle(fetched);
  } catch {
    return readBundle();
  }
}

/** Whether the assistant knows this tenant's catalog at all.
 *
 *  **A map that is entirely empty is a configuration state and not the
 *  cold-start floor** (S8, *Cold start*): step 2's surface-form fallback covers
 *  a *key* the map does not cover, and a fallback that fires on every key is
 *  not a fallback. The two are drawn as different empty states for exactly that
 *  reason. */
export function knowsTheCatalog(bundle: Bundle | null): boolean {
  return !!bundle && Object.keys(bundle.symptom_category_map ?? {}).length > 0;
}
