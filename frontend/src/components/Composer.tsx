import { useRef, useState } from "react";

interface Props {
  onUpload: (file: File) => void;
  onPassword: (password: string) => void;
  awaitingPassword: boolean;
  attemptsRemaining: number | null;
  busy: boolean;
}

/**
 * The composer.
 *
 * It has exactly two modes, because the customer only ever has two things to do: send a
 * document, or supply a password when one is asked for. There is no free-text input —
 * this is a document-verification assistant, not a chatbot, and offering a text box
 * would invite conversation the product cannot hold (`PRODUCT_SPEC.md` §9).
 */
export function Composer({
  onUpload,
  onPassword,
  awaitingPassword,
  attemptsRemaining,
  busy,
}: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [password, setPassword] = useState("");

  function sendPassword() {
    if (!password.trim() || busy) return;
    onPassword(password);
    setPassword("");
  }

  if (awaitingPassword) {
    return (
      <form
        className="border-t border-line bg-white px-3 py-2.5"
        onSubmit={(event) => {
          event.preventDefault();
          sendPassword();
        }}
      >
        <div className="flex items-center gap-2">
          <input
            // `type="password"` so it is not shoulder-surfed or captured by a screen
            // recording of the demo. The value is sent once and never stored (ADR-015).
            type="password"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            // Explicit rather than relying on implicit form submission: pressing Enter
            // is how anyone actually sends this, and it is not a path to leave to a
            // browser default.
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                sendPassword();
              }
            }}
            placeholder="Enter the PDF password"
            className="min-w-0 flex-1 rounded-full border border-line px-4 py-2 text-[13px] outline-none focus:border-accent"
          />
          <button
            type="submit"
            disabled={busy || !password.trim()}
            className="shrink-0 rounded-full bg-accent px-4 py-2 text-[13px] font-medium text-white disabled:opacity-40"
          >
            Send
          </button>
        </div>
        {attemptsRemaining !== null && (
          <p className="mt-1.5 px-1 text-[11px] text-muted">
            {attemptsRemaining} {attemptsRemaining === 1 ? "attempt" : "attempts"} remaining
          </p>
        )}
      </form>
    );
  }

  return (
    <div className="border-t border-line bg-white px-3 py-2.5">
      <input
        ref={fileInput}
        type="file"
        accept="application/pdf,.pdf"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onUpload(file);
          event.target.value = "";
        }}
      />
      <button
        type="button"
        disabled={busy}
        onClick={() => fileInput.current?.click()}
        className="flex w-full items-center gap-2.5 rounded-full border border-line px-4 py-2.5 text-left text-[13px] text-muted transition hover:border-accent hover:text-ink disabled:opacity-40"
      >
        <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor">
          <path
            strokeWidth="2"
            strokeLinecap="round"
            d="M21.4 11.05 12.25 20.2a6 6 0 0 1-8.49-8.49l9.2-9.19a4 4 0 0 1 5.65 5.66l-9.2 9.19a2 2 0 0 1-2.82-2.83l8.49-8.48"
          />
        </svg>
        Attach your bank statement
      </button>
    </div>
  );
}
