import { useCallback, useEffect, useState } from "react";
import { OpsGate } from "../components/OpsGate";
import {
  api,
  ApiError,
  STATIC_DEMO,
  type ApplicationSummary,
  type Handoff,
  type HandoffDocument,
} from "../lib/api";
import { clearOpsSecret, getOpsSecret } from "../lib/ops";

const STATUS_STYLE: Record<string, string> = {
  CLEARED: "text-pass",
  AWAITING_CUSTOMER_ACTION: "text-fix",
  AWAITING_REVIEW: "text-review",
};

/**
 * What the product hands to the lender (`PRODUCT_SPEC.md` §21).
 *
 * A read-only view rather than a push integration: the MVP's job is to prove the
 * document is ready and describe it precisely. Wiring it into a real LOS is an
 * integration exercise that would demonstrate nothing about the hypothesis.
 */
export function SalesHandoff() {
  const [unlocked, setUnlocked] = useState(() => STATIC_DEMO || getOpsSecret() !== null);
  const [error, setError] = useState<string | null>(null);
  const [applications, setApplications] = useState<ApplicationSummary[] | null>(null);
  const [handoff, setHandoff] = useState<Handoff | null>(null);

  const load = useCallback(async () => {
    try {
      setApplications(await api.listApplications());
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401 && !STATIC_DEMO) {
        clearOpsSecret();
        setUnlocked(false);
        setError("That secret was not accepted.");
        return;
      }
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    if (unlocked) void load();
  }, [unlocked, load]);

  if (!unlocked) return <OpsGate onUnlock={() => setUnlocked(true)} error={error} />;

  return (
    <div className="mx-auto flex h-full max-w-5xl">
      <aside className="flex w-72 shrink-0 flex-col border-r border-line">
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-[12px] font-medium tracking-wide text-muted uppercase">
            Applications
          </h2>
          <button
            type="button"
            onClick={() => void load()}
            className="text-[11px] text-muted hover:text-ink"
          >
            Refresh
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {applications?.length === 0 && (
            <p className="px-4 py-6 text-[12px] text-muted">
              No applications yet. Start one from the Customer tab.
            </p>
          )}
          {applications?.map((application) => (
            <button
              key={application.id}
              type="button"
              onClick={() =>
                void api
                  .handoff(application.id)
                  .then(setHandoff)
                  .catch((e: unknown) =>
                    setError(describeError(e)),
                  )
              }
              className={[
                "w-full border-b border-line px-4 py-3 text-left transition hover:bg-surface",
                handoff?.application_reference === application.external_reference
                  ? "bg-surface"
                  : "",
              ].join(" ")}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[13px] font-medium">
                  {application.external_reference}
                </span>
                {application.verified_documents > 0 && (
                  <span className="text-[11px] text-pass">verified</span>
                )}
              </div>
              <p className="mt-0.5 truncate text-[11px] text-muted">
                {application.business_name}
              </p>
              <p
                className={`mt-0.5 text-[11px] ${
                  STATUS_STYLE[application.requirement_status ?? ""] ?? "text-muted"
                }`}
              >
                {(application.requirement_status ?? "—").replace(/_/g, " ").toLowerCase()}
                {application.submission_count > 0 && ` · ${application.submission_count} sent`}
              </p>
            </button>
          ))}
        </div>

        {/*
          Without this the tab is a dead end: a credential the server rejects clears
          itself and returns to the gate, but one the *browser* rejects never reaches the
          server at all, and an operator who mistypes has no way back. Operations has had
          a Lock control since Phase 10; this one was simply missed.
        */}
        {STATIC_DEMO ? (
          <p className="border-t border-line px-4 py-2 text-[11px] leading-relaxed text-muted">
            In the live product this view sits behind an operator credential. Open here so
            you can look around.
          </p>
        ) : (
          <button
            type="button"
            onClick={() => {
              clearOpsSecret();
              setUnlocked(false);
              setHandoff(null);
              setApplications(null);
              setError(null);
            }}
            className="border-t border-line px-4 py-2 text-left text-[11px] text-muted hover:text-ink"
          >
            Lock
          </button>
        )}
      </aside>

      <section className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
        {error && <p className="mb-4 text-[12px] text-fix">{error}</p>}
        {handoff ? <HandoffView handoff={handoff} /> : (
          <p className="py-10 text-[13px] text-muted">
            Select an application to see what would be handed to the lending workflow.
          </p>
        )}
      </section>
    </div>
  );
}

function HandoffView({ handoff }: { handoff: Handoff }) {
  return (
    <div className="space-y-5">
      <header>
        <h2 className="text-base font-semibold">{handoff.application_reference}</h2>
        <p className="mt-0.5 text-[12px] text-muted">
          {handoff.business_name} · {handoff.borrower_name}
        </p>
      </header>

      <section className="grid grid-cols-3 gap-3">
        <Metric label="Requirement" value={handoff.requirement_status.replace(/_/g, " ")} />
        <Metric label="Submissions" value={String(handoff.submission_count)} />
        <Metric
          label="First-time cleared"
          value={
            handoff.first_time_cleared === null
              ? "—"
              : handoff.first_time_cleared
                ? "Yes"
                : "No"
          }
        />
      </section>

      {handoff.documents.length === 0 ? (
        <p className="rounded border border-line bg-surface px-3 py-4 text-[12px] text-muted">
          No verified document yet. Nothing is handed downstream until a document passes —
          that is the point.
        </p>
      ) : (
        handoff.documents.map((document) => (
          <DocumentPanel key={document.document_id} document={document} />
        ))
      )}
    </div>
  );
}

function DocumentPanel({ document }: { document: HandoffDocument }) {
  const checks = document.validation.checks ?? {};
  const override = document.validation.manual_review;

  return (
    <section className="rounded border border-line">
      <div className="flex items-baseline justify-between border-b border-line px-4 py-2.5">
        <h3 className="text-[13px] font-medium">Bank Statement</h3>
        <span className="rounded bg-pass/10 px-2 py-0.5 text-[11px] font-medium text-pass">
          {document.status}
        </span>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 px-4 py-3 text-[12px]">
        <Row label="Bank" value={document.bank_name} />
        <Row label="Account Holder" value={document.account_holder_name} />
        <Row label="Account" value={document.account_number_masked} />
        <Row label="Account Type" value={document.account_type} />
        <Row
          label="Period"
          value={
            document.period_start && document.period_end
              ? `${document.period_start} → ${document.period_end}`
              : null
          }
        />
        <Row label="Pages" value={document.page_count?.toString()} />
        <Row label="Transactions" value={document.transaction_count?.toString()} />
      </dl>

      <div className="border-t border-line px-4 py-3">
        <h4 className="mb-1.5 text-[10px] font-medium tracking-widest text-muted uppercase">
          Validation
        </h4>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-0.5 text-[12px]">
          {Object.entries(checks).map(([label, status]) => (
            <div key={label} className="contents">
              <dt className="text-muted">{label}</dt>
              <dd className={status === "PASS" ? "text-pass" : "text-fix"}>{status}</dd>
            </div>
          ))}
          <div className="contents">
            <dt className="text-muted">Confidence</dt>
            <dd>
              {document.validation.confidence === null ||
              document.validation.confidence === undefined
                ? "—"
                : `${Math.round(document.validation.confidence * 100)}%`}
            </dd>
          </div>
        </dl>

        {override && (
          // Without this, a VERIFIED document with a failing check reads as a
          // contradiction. Credit needs to know a person made the call, and who.
          <p className="mt-2 rounded bg-review/5 px-2.5 py-2 text-[11px] text-review">
            Manually reviewed: {override.reason_code.replace(/_/g, " ").toLowerCase()} —{" "}
            {override.action === "ACCEPT" ? "accepted" : "returned"} by{" "}
            {override.reviewer_id ?? "an operator"}
            {override.note ? ` · “${override.note}”` : ""}
          </p>
        )}
      </div>

      <div className="border-t border-line px-4 py-3">
        {STATIC_DEMO ? (
          // There is no object store behind a static build, so a download link would
          // 404. Saying why is better than a dead control.
          <p className="rounded border border-line bg-surface px-3 py-2 text-[11px] leading-relaxed text-muted">
            The document itself is not part of this recording. In the live product this is
            a short-lived signed link; documents are never publicly addressable.
          </p>
        ) : (
          <a
            href={`/api${document.download_url}`}
            target="_blank"
            rel="noreferrer"
            className="inline-block rounded border border-line px-3 py-1.5 text-[12px] hover:border-accent"
          >
            Download verified document ↗
          </a>
        )}
      </div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="contents">
      <dt className="text-muted">{label}</dt>
      <dd>{value ?? "—"}</dd>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-line px-3 py-2">
      <p className="text-[10px] tracking-wide text-muted uppercase">{label}</p>
      <p className="text-[13px] capitalize">{value.toLowerCase()}</p>
    </div>
  );
}


/** Never render a raw object in a banner: it tells the reader nothing. */
function describeError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string") return error;
  return "Something went wrong. Please try again.";
}
