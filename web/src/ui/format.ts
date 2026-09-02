/**
 * §A.11 · the one number and date formatter.
 *
 * There is no `i18n` runtime and no second locale (architecture §1), so the
 * locale is a constant and **every** number in the product goes through this
 * module. No call site calls `toLocaleString` on its own.
 *
 * The `$` is prefixed here rather than by `Intl`'s currency style: ICU emits a
 * locale- and version-dependent space between symbol and figure for COP, and a
 * figure whose spacing changes with a Node upgrade is a figure that breaks a
 * column.
 */

const LOCALE = "es-CO";

/** U+2212 MINUS SIGN, never a hyphen. */
const MINUS = "−";
/** U+00A0, so `$9,4 M` and `hace 4 s` never wrap between figure and meaning. */
const NBSP = " ";
/** U+00B7, the clause separator in a label. */
export const DOT = "·";
/** U+00D7, spaced, for a presentation or a quantity. */
export const TIMES = "×";

const MILLION = 1_000_000;

const groups = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat(LOCALE, {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

function signed(value: number, body: string) {
  return value < 0 ? `${MINUS}${body}` : body;
}

/** `4.284` — thousands dot, no decimals. */
export function count(value: number): string {
  return signed(value, groups.format(Math.abs(value)));
}

/** `24,8` — decimal comma, one place. */
export function decimal(value: number): string {
  return signed(value, oneDecimal.format(Math.abs(value)));
}

/**
 * `$15.600` — prefixed, unspaced, no decimals. Above a million in a display
 * position it abbreviates to `$9,4 M`; in a table cell it never does, because a
 * table cell is where the exact figure is read.
 */
export function money(
  value: number,
  options?: { abbreviate?: boolean },
): string {
  const magnitude = Math.abs(value);
  if (options?.abbreviate && magnitude >= MILLION) {
    return signed(value, `$${oneDecimal.format(magnitude / MILLION)}${NBSP}M`);
  }
  return signed(value, `$${groups.format(Math.round(magnitude))}`);
}

/** `24,8%` — no space before the sign. */
export function percent(value: number): string {
  return `${decimal(value)}%`;
}

/** `+1,9 pp` — percentage points, with a space. */
export function points(value: number): string {
  const body = `${decimal(Math.abs(value))}${NBSP}pp`;
  return value < 0 ? `${MINUS}${body}` : `+${body}`;
}

/** `27,5 g` — a non-breaking space before the unit. */
export function withUnit(value: number, unit: string): string {
  return `${decimal(value)}${NBSP}${unit}`;
}

/** `1-15 de 4.284` — an ASCII hyphen, unspaced. */
export function range(first: number, last: number, total: number): string {
  return `${count(first)}-${count(last)} de ${count(total)}`;
}

const two = (value: number) => String(value).padStart(2, "0");

/** `03/2027` — a lot's expiry. */
export function monthYear(value: Date | string): string {
  const date = asDate(value);
  return `${two(date.getMonth() + 1)}/${date.getFullYear()}`;
}

/** `12/09` — a day and a month, for an invitation's expiry. */
export function dayMonth(value: Date | string): string {
  const date = asDate(value);
  return `${two(date.getDate())}/${two(date.getMonth() + 1)}`;
}

/** `09:14` — 24-hour. */
export function time(value: Date | string): string {
  const date = asDate(value);
  return `${two(date.getHours())}:${two(date.getMinutes())}`;
}

/** `al 31/08 06:00` — the absolute stamp the relative ladder falls back to. */
export function stamp(value: Date | string): string {
  const date = asDate(value);
  return `al ${dayMonth(date)} ${time(date)}`;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const LADDER_LIMIT = 12 * HOUR;

/**
 * §B.9.1's relative-time ladder, and §B.9.2 uses the same one: under 60 s →
 * `hace 4 s`, under 60 min → `hace 3 min`, under 12 h → `hace 2 h`, and at or
 * beyond 12 h the absolute stamp.
 */
export function since(value: Date | string, now: Date = new Date()): string {
  const date = asDate(value);
  const elapsed = Math.max(0, now.getTime() - date.getTime());
  if (elapsed >= LADDER_LIMIT) return stamp(date);
  if (elapsed >= HOUR) return `hace ${Math.floor(elapsed / HOUR)}${NBSP}h`;
  if (elapsed >= MINUTE)
    return `hace ${Math.floor(elapsed / MINUTE)}${NBSP}min`;
  return `hace ${Math.floor(elapsed / 1000)}${NBSP}s`;
}

/** `2026-09-02` — a date the server sent with no time in it. */
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

function asDate(value: Date | string): Date {
  if (value instanceof Date) return value;
  // A bare `YYYY-MM-DD` is parsed by `new Date()` as **UTC midnight**, and
  // every getter below reads local calendar fields — so in Bogotá (UTC−5) an
  // expiry of `2026-09-02` renders as `01/09`. Every date-only value in this
  // product is a day the pharmacy means, not an instant, so it is built in the
  // local calendar. A timestamp with a time in it keeps its own parsing.
  const parts = DATE_ONLY.exec(value);
  if (parts) {
    return new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
  }
  return new Date(value);
}

/**
 * `2026-09-02` — **the pharmacy's calendar day, not the machine's.**
 *
 * A price window is `[effective_from, effective_to)` over days the droguería
 * means, and the server evaluates them in `America/Bogota`. A till whose OS
 * clock is set to UTC — a common kiosk default — would otherwise roll over to
 * tomorrow at 19:00 Bogotá and charge tomorrow's price for five hours, on the
 * one figure a customer is about to pay.
 *
 * The timezone is a constant for the same reason the locale is: there is no
 * `i18n` runtime and no second locale (architecture §1). A network that opens
 * outside Colombia is a change here and a `tenants.settings` key, not a
 * per-call-site fix.
 */
const BUSINESS_TIMEZONE = "America/Bogota";

const businessDayParts = new Intl.DateTimeFormat("en-CA", {
  timeZone: BUSINESS_TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export function businessDay(at: Date = new Date()): string {
  // `en-CA` formats as `YYYY-MM-DD`, which is the shape every date-only value
  // in this product already has on the wire.
  return businessDayParts.format(at);
}

export const NON_BREAKING_SPACE = NBSP;
