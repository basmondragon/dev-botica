const SIGN_IN = "/login";

/**
 * The path to return to after signing in, or nothing. A destination is only
 * ever a path back into this application: an absolute URL, a protocol-relative
 * `//host` and a backslash the browser reads as a slash all leave the origin,
 * and the sign-in page itself would only send the person back to where they
 * already are.
 */
export function returnPath(value: unknown): string | undefined {
  if (typeof value !== "string" || !value.startsWith("/")) return undefined;
  if (value[1] === "/" || value[1] === "\\") return undefined;
  return value.split(/[?#]/u)[0] === SIGN_IN ? undefined : value;
}
