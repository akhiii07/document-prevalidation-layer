import { useState } from "react";
import { STATIC_DEMO } from "../lib/api";
import { isHeaderSafe, normaliseSecret, setOpsSecret } from "../lib/ops";

/**
 * The operator credential gate.
 *
 * Deliberately plain, because it is deliberately not a login: there is no user, no
 * session and no identity behind it — one shared secret, typed rather than shipped
 * (ADR-011). Dressing it up as a sign-in page would misrepresent what it does, and the
 * note below says so where an operator will actually read it.
 */
export function OpsGate({ onUnlock, error }: { onUnlock: () => void; error?: string | null }) {
  const [secret, setSecret] = useState("");
  const [rejected, setRejected] = useState<string | null>(null);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const cleaned = normaliseSecret(secret);
    if (!cleaned) return;

    // Checked here rather than left to `fetch`, which rejects an unsendable header value
    // by throwing before the request exists — a failure that arrives as
    // "String contains non ISO-8859-1 code point" on a screen with no way back.
    if (!isHeaderSafe(cleaned)) {
      setRejected(
        "That secret contains a character that can't be sent in a request header — " +
          "usually something picked up by copying from formatted text. Try typing it instead.",
      );
      return;
    }

    setRejected(null);
    setOpsSecret(cleaned);
    onUnlock();
  }

  return (
    <div className="mx-auto max-w-sm px-6 py-20">
      <h2 className="text-base font-semibold">Operations</h2>
      <p className="mt-1 text-[13px] text-muted">
        Enter the operations secret to view the review queue.
      </p>

      <form className="mt-5 space-y-2" onSubmit={submit}>
        <input
          type="password"
          autoFocus
          value={secret}
          onChange={(event) => {
            setSecret(event.target.value);
            if (rejected) setRejected(null);
          }}
          placeholder="Operations secret"
          className="w-full rounded border border-line px-3 py-2 text-[13px] outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={!normaliseSecret(secret)}
          className="w-full rounded bg-accent px-3 py-2 text-[13px] font-medium text-white disabled:opacity-40"
        >
          Continue
        </button>
      </form>

      {(rejected || error) && (
        <p className="mt-3 text-[12px] leading-relaxed text-fix">{rejected ?? error}</p>
      )}

      {STATIC_DEMO && (
        // The recorded build authenticates nothing -- there is no server to authenticate
        // against. Saying so is better than letting a visitor guess at a credential that
        // is not being checked.
        <p className="mt-4 rounded border border-line bg-surface px-3 py-2 text-[11px] leading-relaxed text-muted">
          In this recorded demo any value opens the console — there is no server to check
          it against. The live product gates these tabs on a shared secret.
        </p>
      )}

      <p className="mt-6 border-t border-line pt-4 text-[11px] leading-relaxed text-muted">
        Prototype access control: a single shared secret, not per-operator identity. A real
        deployment needs authenticated operators so review decisions are attributable to a
        person. Default in <code>.env.example</code>:{" "}
        <code className="text-ink">dev-ops-secret-change-me</code>
      </p>
    </div>
  );
}
