import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema.gen";

function readCookie(name: string) {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match?.[1] ? decodeURIComponent(match[1]) : undefined;
}

const csrf: Middleware = {
  onRequest({ request }) {
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      const token = readCookie("csrftoken");
      if (token) request.headers.set("X-CSRFToken", token);
    }
    return request;
  },
};

export const api = createClient<paths>({
  baseUrl: "/",
  credentials: "same-origin",
});
api.use(csrf);

/**
 * §B.10.3 · every error names the operation, the entity and the recovery, and
 * carries the correlation id the server stamped. A raw exception or vendor
 * payload never reaches a user.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly requestId?: string,
    /** §B.10.3 · which line of a multi-line entry was refused, and which
     *  control on it. Present only where the endpoint says so; a caller that
     *  ignores both still renders a correct region-scope error, which is what
     *  keeps the pair additive. */
    readonly line?: number,
    readonly field?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const UNREACHABLE = "No pudimos conectar con el servidor.";

export function toApiError(
  response: Response | undefined,
  body: unknown,
  fallback: string,
): ApiError {
  if (!response) return new ApiError(UNREACHABLE, 0);
  const detail =
    body &&
    typeof body === "object" &&
    "detail" in body &&
    typeof body.detail === "string"
      ? body.detail
      : fallback;
  const scoped =
    body && typeof body === "object" ? (body as Record<string, unknown>) : {};
  return new ApiError(
    detail,
    response.status,
    response.headers.get("x-request-id") ?? undefined,
    typeof scoped.line === "number" ? scoped.line : undefined,
    typeof scoped.field === "string" ? scoped.field : undefined,
  );
}

const ALLAUTH = "/_allauth/browser/v1";

async function allauth(path: string, init: RequestInit) {
  const token = readCookie("csrftoken");
  return fetch(`${ALLAUTH}${path}`, {
    ...init,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-CSRFToken": token } : {}),
      ...init.headers,
    },
  });
}

type AllauthError = { code?: string; message?: string };

function allauthErrors(body: unknown): AllauthError[] {
  if (!body || typeof body !== "object" || !("errors" in body)) return [];
  const errors = (body as { errors?: unknown }).errors;
  return Array.isArray(errors) ? (errors as AllauthError[]) : [];
}

/**
 * The CSRF cookie. Django's shell view sets it on every HTML request, so in
 * production it is always there before anything is typed. In development the
 * page is served by Vite and Django has never seen the browser, so the first
 * write would be refused; allauth's own config endpoint is what hands one out.
 */
async function ensureCsrfCookie() {
  if (readCookie("csrftoken")) return;
  await allauth("/config", { method: "GET" }).catch(() => undefined);
}

/**
 * §B.8.4·5 · name the refusal, never `Credenciales inválidas`. A generic
 * message tells an attacker nothing and tells a cashier nothing either.
 */
export async function signIn(email: string, password: string) {
  await ensureCsrfCookie();
  let response: Response;
  try {
    response = await allauth("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  } catch {
    throw new ApiError(UNREACHABLE, 0);
  }
  if (response.ok || response.status === 409) return;

  const body: unknown = await response.json().catch(() => null);
  const messages = allauthErrors(body)
    .map((error) => error.message ?? "")
    .filter(Boolean);

  // The server names the refusal and the card shows what it named. Folding
  // them back into one message here would undo the whole point of §B.8.4·5.
  const named = messages.find((message) => message.length > 0);
  if (named) throw new ApiError(named, response.status);

  if (response.status === 429) {
    throw new ApiError(
      "Demasiados intentos. Espere cinco minutos e intente de nuevo: esto es " +
        "un límite, no una contraseña equivocada.",
      response.status,
    );
  }
  throw new ApiError(
    "No encontramos una cuenta con ese correo.",
    response.status,
  );
}

export async function signOut() {
  await allauth("/auth/session", { method: "DELETE" });
}
