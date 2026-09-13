import { useCallback, useEffect, useState } from "react";
import { OpsGate } from "../components/OpsGate";
import { ReviewDetailPanel } from "../components/ReviewDetailPanel";
import { api, ApiError, type ReviewDetail, type ReviewSummary } from "../lib/api";
import { clearOpsSecret, getOpsSecret } from "../lib/ops";

/**
 * The review queue (`PRODUCT_SPEC.md` §20).
 *
 * Deliberately minimal — the purpose is to demonstrate REVIEW handling, not to be an
 * operations platform. What it does have to get right is the card: a reviewer needs the
 * evidence, not a score.
 */
export function Operations() {
  const [unlocked, setUnlocked] = useState(() => getOpsSecret() !== null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [queue, setQueue] = useState<ReviewSummary[] | null>(null);
  const [selected, setSelected] = useState<ReviewDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [resolved, setResolved] = useState<string | null>(null);

  const loadQueue = useCallback(async () => {
    try {
      setQueue(await api.reviews());
      setAuthError(null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        clearOpsSecret();
        setUnlocked(false);
        setAuthError("That secret was not accepted.");
        return;
      }
      setAuthError(describeError(error));
    }
  }, []);

  useEffect(() => {
    if (unlocked) void loadQueue();
  }, [unlocked, loadQueue]);

  async function open(review: ReviewSummary) {
    setResolved(null);
    setSelected(await api.review(review.id));
  }

  async function decide(
    action: "ACCEPT" | "REQUEST_NEW_DOCUMENT",
    reviewerId: string,
    note: string,
  ) {
    if (!selected) return;
    setBusy(true);
    try {
      const updated = await api.decide(selected.id, action, reviewerId, note);
      setSelected(null);
      setResolved(
        action === "ACCEPT"
          ? `${updated.application_reference} accepted — the customer has been told and Sales notified.`
          : `${updated.application_reference} sent back — the customer has been asked for a new document.`,
      );
      await loadQueue();
    } catch (error) {
      setAuthError(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  if (!unlocked) {
    return <OpsGate onUnlock={() => setUnlocked(true)} error={authError} />;
  }

  return (
    <div className="mx-auto flex h-full max-w-5xl">
      <aside className="flex w-72 shrink-0 flex-col border-r border-line">
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-[12px] font-medium tracking-wide text-muted uppercase">
            Review queue
          </h2>
          <span className="rounded bg-review/10 px-1.5 py-0.5 text-[11px] font-medium text-review">
            {queue?.length ?? "—"}
          </span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {queue?.length === 0 && (
            <p className="px-4 py-6 text-[12px] text-muted">
              Nothing waiting. Send a document that lands in REVIEW from the Customer tab —
              try <code className="text-ink">balance_break_icici</code> or{" "}
              <code className="text-ink">identity_mismatch_axis</code>.
            </p>
          )}

          {queue?.map((review) => (
            <button
              key={review.id}
              type="button"
              onClick={() => void open(review)}
              className={[
                "w-full border-b border-line px-4 py-3 text-left transition hover:bg-surface",
                selected?.id === review.id ? "bg-surface" : "",
              ].join(" ")}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[13px] font-medium">{review.application_reference}</span>
                <span className="text-[11px] text-muted">{age(review.created_at)}</span>
              </div>
              <p className="mt-0.5 truncate text-[11px] text-review">
                {review.reason_code.replace(/_/g, " ")}
              </p>
              <p className="mt-0.5 truncate text-[11px] text-muted">{review.business_name}</p>
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={() => {
            clearOpsSecret();
            setUnlocked(false);
          }}
          className="border-t border-line px-4 py-2 text-left text-[11px] text-muted hover:text-ink"
        >
          Lock
        </button>
      </aside>

      <section className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
        {authError && <p className="mb-4 text-[12px] text-fix">{authError}</p>}

        {resolved && (
          <p className="mb-4 rounded border border-pass/30 bg-pass/5 px-3 py-2 text-[12px] text-pass">
            {resolved}
          </p>
        )}

        {selected ? (
          <ReviewDetailPanel review={selected} onDecide={decide} busy={busy} />
        ) : (
          !resolved && (
            <p className="py-10 text-[13px] text-muted">
              Select a review to see why the system declined to decide.
            </p>
          )
        )}
      </section>
    </div>
  );
}

/** Age matters here: the queue is worked oldest-first precisely to avoid stranding cases. */
function age(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}


/** Never render a raw object in a banner: it tells the reader nothing. */
function describeError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string") return error;
  return "Something went wrong. Please try again.";
}
