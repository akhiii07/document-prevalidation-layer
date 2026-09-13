import { useState } from "react";
import { setOpsSecret } from "../lib/ops";

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

  return (
    <div className="mx-auto max-w-sm px-6 py-20">
      <h2 className="text-base font-semibold">Operations</h2>
      <p className="mt-1 text-[13px] text-muted">
        Enter the operations secret to view the review queue.
      </p>

      <form
        className="mt-5 space-y-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!secret.trim()) return;
          setOpsSecret(secret.trim());
          onUnlock();
        }}
      >
        <input
          type="password"
          autoFocus
          value={secret}
          onChange={(event) => setSecret(event.target.value)}
          placeholder="Operations secret"
          className="w-full rounded border border-line px-3 py-2 text-[13px] outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={!secret.trim()}
          className="w-full rounded bg-accent px-3 py-2 text-[13px] font-medium text-white disabled:opacity-40"
        >
          Continue
        </button>
      </form>

      {error && <p className="mt-3 text-[12px] text-fix">{error}</p>}

      <p className="mt-6 border-t border-line pt-4 text-[11px] leading-relaxed text-muted">
        Prototype access control: a single shared secret, not per-operator identity. A real
        deployment needs authenticated operators so review decisions are attributable to a
        person. Default in <code>.env.example</code>:{" "}
        <code className="text-ink">dev-ops-secret-change-me</code>
      </p>
    </div>
  );
}
