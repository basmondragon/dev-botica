import { APP_VERSION } from "@/app-version";
import type { components } from "@/api/schema.gen";
import type { DeviceRecord } from "./device";

/**
 * The four calls a till makes, and the headers every one of them carries.
 *
 * **The session and the key, and neither alone is sufficient** (A4). The
 * session travels as the cookie every request in the product carries; the key
 * travels here. The device's own wall clock, its application version and
 * whether its browser granted persistent storage ride along on every call, so
 * skew is measured on every interaction and needs no endpoint of its own.
 *
 * `fetch` rather than the generated client, because these four are the only
 * calls in the product that must distinguish **"the server refused"** from
 * **"we never reached the server"** — which is the whole difference between
 * `degraded` and `offline` (§B.9.1), and an exception is where that lives.
 */

export type RegistryResponse = components["schemas"]["RegistryOut"];
export type PullResponse = components["schemas"]["PullOut"];
export type PushResponse = components["schemas"]["PushOut"];
export type DigestResponse = components["schemas"]["DigestOut"];

/** A call that reached the server and was refused. Carries the status, so the
 *  state machine can name the reason rather than saying `Error`. */
export class ServerRefused extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    /** §B.10.3 · the id the server stamped, so a refusal a cashier reports has
     *  something a support engineer can chase. */
    readonly requestId = "",
  ) {
    super(detail);
    this.name = "ServerRefused";
  }
}

/** A call that never got an answer. **This is `offline`, not `degraded`.** */
export class Unreachable extends Error {
  constructor() {
    super("No pudimos conectar con el servidor.");
    this.name = "Unreachable";
  }
}

function readCookie(name: string) {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match?.[1] ? decodeURIComponent(match[1]) : undefined;
}

function headers(device: DeviceRecord, extra?: HeadersInit): Headers {
  const built = new Headers(extra);
  built.set("X-Botica-Device-Key", device.key);
  // The till's own wall clock, sent as it stands. The server computes the skew
  // and stores it; **nothing ever corrects `occurred_at` from it** (§5 rule 4).
  built.set("X-Botica-Device-Clock", new Date().toISOString());
  built.set("X-Botica-App-Version", APP_VERSION);
  if (device.persisted !== null) {
    built.set("X-Botica-Storage-Persisted", String(device.persisted));
  }
  return built;
}

async function call<T>(
  device: DeviceRecord,
  path: string,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: headers(device, init?.headers),
    });
  } catch {
    throw new Unreachable();
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : "El servidor rechazó la sincronización.";
    throw new ServerRefused(
      response.status,
      detail,
      response.headers.get("x-request-id") ?? "",
    );
  }
  return (await response.json()) as T;
}

export function fetchRegistry(device: DeviceRecord) {
  return call<RegistryResponse>(device, "/api/sync/registry");
}

export function fetchDigest(device: DeviceRecord) {
  return call<DigestResponse>(device, "/api/sync/digest");
}

export function fetchPull(
  device: DeviceRecord,
  collection: string,
  checkpoint: { updated_at: string; id: string } | null,
  limit: number,
) {
  const query = new URLSearchParams({ collection, limit: String(limit) });
  if (checkpoint) {
    query.set("updated_at", checkpoint.updated_at);
    query.set("id", checkpoint.id);
  }
  return call<PullResponse>(device, `/api/sync/pull?${query.toString()}`);
}

export function postPush(
  device: DeviceRecord,
  body: {
    batch_id: string;
    client_time: string;
    rows: {
      collection: string;
      client_uuid: string;
      occurred_at: string;
      payload: Record<string, unknown>;
    }[];
  },
) {
  const token = readCookie("csrftoken");
  return call<PushResponse>(device, "/api/sync/push", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-CSRFToken": token } : {}),
    },
    body: JSON.stringify(body),
  });
}
