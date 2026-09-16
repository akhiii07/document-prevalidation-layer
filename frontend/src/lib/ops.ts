/**
 * Operations credential handling.
 *
 * The shared secret is **typed by the operator**, never shipped in the bundle — that
 * would put a credential in source control and hand it to every visitor
 * (`PRODUCT_SPEC.md` §12).
 *
 * It is held in `sessionStorage`, which survives a refresh and dies with the tab. That
 * is a deliberate prototype compromise and not what a real deployment should do:
 * `sessionStorage` is readable by any script on the page, so an XSS bug would leak it.
 * The production shape is per-operator authentication returning an HttpOnly session
 * cookie, which is ADR-011's stated endpoint.
 */

const STORAGE_KEY = "docverify.opsSecret";

/**
 * Anything outside printable Latin-1 cannot be sent in a request header.
 *
 * `fetch` enforces this itself, but it does so by throwing a `TypeError` *before the
 * request leaves the browser* — so the failure never reaches any of our error handling
 * and surfaces as "String contains non ISO-8859-1 code point", which tells an operator
 * nothing they can act on. The ranges below are printable ASCII and the printable half
 * of the Latin-1 supplement; the gap between them is C1 control characters, which break
 * headers just as thoroughly.
 */
const HEADER_UNSAFE = /[^ -~ -ÿ]/;

/**
 * Invisible characters that ride along on a paste and are never intended.
 *
 * Copying a credential out of formatted text — a chat message, a wiki, a PDF — routinely
 * picks up a zero-width space or a byte-order mark. Stripping them silently is right:
 * the operator did not type them, cannot see them, and would have no way to find them.
 */
const INVISIBLE = /[​-‍⁠﻿]/g;

/** What the operator meant, rather than what their clipboard produced. */
export function normaliseSecret(raw: string): string {
  return raw.replace(INVISIBLE, "").trim();
}

export function isHeaderSafe(secret: string): boolean {
  return secret.length > 0 && !HEADER_UNSAFE.test(secret);
}

export function getOpsSecret(): string | null {
  return sessionStorage.getItem(STORAGE_KEY);
}

export function setOpsSecret(secret: string): void {
  sessionStorage.setItem(STORAGE_KEY, normaliseSecret(secret));
}

export function clearOpsSecret(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}

export function opsHeaders(): Record<string, string> {
  const secret = getOpsSecret();
  if (secret === null) return {};

  if (!isHeaderSafe(secret)) {
    // A credential that cannot be sent is worse than no credential at all. Sending it
    // throws inside `fetch`, which no caller is expecting; dropping it produces a plain
    // 401, which every caller already knows how to recover from — it clears the secret
    // and returns the operator to the gate. Degrade to the handled failure.
    clearOpsSecret();
    return {};
  }

  return { "X-Ops-Secret": secret };
}
