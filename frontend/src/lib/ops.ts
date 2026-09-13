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

export function getOpsSecret(): string | null {
  return sessionStorage.getItem(STORAGE_KEY);
}

export function setOpsSecret(secret: string): void {
  sessionStorage.setItem(STORAGE_KEY, secret);
}

export function clearOpsSecret(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}

export function opsHeaders(): Record<string, string> {
  const secret = getOpsSecret();
  return secret ? { "X-Ops-Secret": secret } : {};
}
