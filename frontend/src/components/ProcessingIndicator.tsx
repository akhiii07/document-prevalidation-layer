/**
 * The "checking your statement" state.
 *
 * Shown while the document is in flight. The customer must never face a blank wait —
 * an opaque delay is the experience this product exists to remove, and reproducing it
 * in the UI would be a poor joke.
 *
 * The `hint` exists because a scanned document takes tens of seconds to read, and a
 * spinner that says the same thing at 90 seconds as it did at 2 is indistinguishable
 * from one that has hung. Someone testing this waited two minutes on "Received your
 * document" and reasonably concluded nothing was working.
 */
export function ProcessingIndicator({ label, hint }: { label: string; hint?: string | null }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] rounded-lg bg-white px-3 py-2 shadow-sm">
        <div className="flex items-center gap-2">
          <span className="flex gap-1">
            {[0, 1, 2].map((index) => (
              <span
                key={index}
                className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted"
                style={{ animationDelay: `${index * 140}ms` }}
              />
            ))}
          </span>
          <span className="text-[12px] text-muted">{label}</span>
        </div>
        {hint && <p className="mt-1 text-[11px] leading-snug text-muted/80">{hint}</p>}
      </div>
    </div>
  );
}
