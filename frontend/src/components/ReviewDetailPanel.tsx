import { useState } from "react";
import type { ReviewDetail, RuleResult } from "../lib/api";

const STATUS_STYLE: Record<RuleResult["status"], string> = {
  PASS: "text-pass",
  FAIL: "text-fix",
  NOT_EVALUATED: "text-muted",
  SIGNAL: "text-review",
};

const STATUS_GLYPH: Record<RuleResult["status"], string> = {
  PASS: "✓",
  FAIL: "✕",
  NOT_EVALUATED: "–",
  SIGNAL: "!",
};

/**
 * The review card (`PRODUCT_SPEC.md` §20).
 *
 * The spec's sketch shows a reason and a confidence percentage. That is not enough to
 * decide anything (ADR-009), so this shows the confidence *components* and every rule
 * that ran with the evidence it produced. A reviewer's question is "why is this on my
 * desk, and what did the system actually see" — a single number answers neither.
 */
export function ReviewDetailPanel({
  review,
  onDecide,
  busy,
}: {
  review: ReviewDetail;
  onDecide: (action: "ACCEPT" | "REQUEST_NEW_DOCUMENT", reviewerId: string, note: string) => void;
  busy: boolean;
}) {
  const [reviewerId, setReviewerId] = useState("ops-anita");
  const [note, setNote] = useState("");

  const breakdown = review.confidence_breakdown;
  const rules = review.rule_results ?? [];
  const failing = rules.filter((rule) => rule.status === "FAIL");
  const signals = rules.filter((rule) => rule.status === "SIGNAL");

  return (
    <div className="space-y-5">
      <header>
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold">{review.application_reference}</h2>
          <span className="rounded bg-review/10 px-2 py-0.5 text-[11px] font-medium text-review">
            {review.reason_code.replace(/_/g, " ")}
          </span>
        </div>
        <p className="mt-0.5 text-[12px] text-muted">
          {review.business_name} · {review.borrower_name}
        </p>
        <p className="mt-0.5 text-[12px] text-muted">
          Bank statement · submission {review.submission_index} · {review.original_filename}
        </p>
      </header>

      {breakdown && (
        <section>
          <SectionTitle>Confidence</SectionTitle>
          <div className="grid grid-cols-3 gap-3">
            <Metric label="Composite" value={breakdown.composite} emphasise />
            <Metric label="Field score" value={breakdown.field_score} />
            <Metric label="Rule coverage" value={breakdown.rule_coverage} />
          </div>
          <p className="mt-1.5 text-[11px] text-muted">
            Rule coverage is the share of checks that could be evaluated at all. A high
            field score with low coverage means the document read cleanly but most rules
            had nothing to run against.
          </p>
        </section>
      )}

      {(failing.length > 0 || signals.length > 0) && (
        <section>
          <SectionTitle>What the system found</SectionTitle>
          <div className="space-y-2">
            {[...failing, ...signals].map((rule) => (
              <Finding key={rule.rule_id} rule={rule} />
            ))}
          </div>
        </section>
      )}

      {review.extracted && <Extracted data={review.extracted} />}

      <section>
        <SectionTitle>All checks</SectionTitle>
        <ul className="space-y-0.5">
          {rules.map((rule) => (
            <li key={rule.rule_id} className="flex items-center gap-2 text-[12px]">
              <span className={`w-3 text-center ${STATUS_STYLE[rule.status]}`}>
                {STATUS_GLYPH[rule.status]}
              </span>
              <span className="w-20 shrink-0 font-mono text-[11px] text-muted">
                {rule.rule_id}
              </span>
              <span className={STATUS_STYLE[rule.status]}>
                {rule.reason_code?.replace(/_/g, " ") ?? rule.status.replace(/_/g, " ")}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <SectionTitle>Document</SectionTitle>
        <a
          href={`/api${review.download_url}`}
          target="_blank"
          rel="noreferrer"
          className="inline-block rounded border border-line px-3 py-1.5 text-[12px] hover:border-accent"
        >
          View document ↗
        </a>
        <p className="mt-1.5 text-[11px] text-muted">
          Opens through a short-lived signed link. Documents are never publicly addressable.
        </p>
      </section>

      <section className="border-t border-line pt-4">
        <SectionTitle>Decision</SectionTitle>
        <div className="space-y-2">
          <div className="flex gap-2">
            <input
              value={reviewerId}
              onChange={(event) => setReviewerId(event.target.value)}
              placeholder="Reviewer"
              className="w-32 rounded border border-line px-2 py-1.5 text-[12px] outline-none focus:border-accent"
            />
            <input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Note (optional)"
              className="min-w-0 flex-1 rounded border border-line px-2 py-1.5 text-[12px] outline-none focus:border-accent"
            />
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => onDecide("ACCEPT", reviewerId, note)}
              className="flex-1 rounded bg-pass px-3 py-2 text-[12px] font-medium text-white disabled:opacity-40"
            >
              Accept document
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onDecide("REQUEST_NEW_DOCUMENT", reviewerId, note)}
              className="flex-1 rounded border border-fix px-3 py-2 text-[12px] font-medium text-fix disabled:opacity-40"
            >
              Request new document
            </button>
          </div>
          <p className="text-[11px] text-muted">
            Both decisions are audited and both reach the customer. Accepting clears the
            requirement and still counts as first-time clearance — the customer sent a
            usable document; the uncertainty was ours.
          </p>
        </div>
      </section>
    </div>
  );
}

function Finding({ rule }: { rule: RuleResult }) {
  const entries = Object.entries(rule.evidence).filter(
    ([key, value]) =>
      key !== "breaks" && value !== null && value !== undefined && value !== "",
  );
  const breaks = Array.isArray(rule.evidence.breaks)
    ? (rule.evidence.breaks as Record<string, unknown>[])
    : null;

  return (
    <div className="rounded border border-line bg-surface px-3 py-2">
      <div className="flex items-center gap-2">
        <span className={`text-[12px] ${STATUS_STYLE[rule.status]}`}>
          {STATUS_GLYPH[rule.status]}
        </span>
        <span className="text-[12px] font-medium">
          {rule.reason_code?.replace(/_/g, " ") ?? "signal"}
        </span>
        <span className="font-mono text-[10px] text-muted">{rule.rule_id}</span>
      </div>

      <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
        {entries.slice(0, 8).map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="text-muted">{key.replace(/_/g, " ")}</dt>
            <dd className="break-words">{format(value, key)}</dd>
          </div>
        ))}
      </dl>

      {breaks && breaks.length > 0 && <BalanceBreaks breaks={breaks} />}
    </div>
  );
}

/**
 * Where the balance chain failed.
 *
 * Rendered as a table with rupee amounts rather than the raw evidence object: an
 * operator reading `expected_paise 428321021` has to do mental arithmetic before they
 * can judge anything, and the whole point of showing evidence is that it can be judged.
 */
function BalanceBreaks({ breaks }: { breaks: Record<string, unknown>[] }) {
  return (
    <table className="mt-2 w-full text-[11px]">
      <thead>
        <tr className="text-left text-muted">
          <th className="font-normal">row</th>
          <th className="font-normal">page</th>
          <th className="font-normal">date</th>
          <th className="text-right font-normal">expected</th>
          <th className="text-right font-normal">stated</th>
          <th className="text-right font-normal">difference</th>
        </tr>
      </thead>
      <tbody>
        {breaks.map((entry, index) => (
          <tr key={index} className="border-t border-line/60">
            <td>{String(entry.index ?? "—")}</td>
            <td>{String(entry.page ?? "—")}</td>
            <td>{String(entry.txn_date ?? "—")}</td>
            <td className="text-right tabular-nums">{rupees(entry.expected_paise)}</td>
            <td className="text-right tabular-nums">{rupees(entry.stated_paise)}</td>
            <td className="text-right tabular-nums text-fix">
              {rupees(entry.difference_paise)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Indian digit grouping: 1,23,456.78, which is how the statement itself prints it. */
function rupees(value: unknown): string {
  if (typeof value !== "number") return "—";
  const negative = value < 0;
  const whole = Math.floor(Math.abs(value) / 100);
  const paise = String(Math.abs(value) % 100).padStart(2, "0");

  const digits = String(whole);
  let grouped = digits;
  if (digits.length > 3) {
    let head = digits.slice(0, -3);
    const parts = [digits.slice(-3)];
    while (head.length > 2) {
      parts.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) parts.unshift(head);
    grouped = parts.join(",");
  }
  return `${negative ? "−" : ""}₹${grouped}.${paise}`;
}

function Extracted({ data }: { data: Record<string, unknown> }) {
  const fields: [string, unknown][] = [
    ["Bank", data.bank_name],
    ["Account holder", data.account_holder_name],
    ["Account", data.account_number_masked],
    ["Account type", data.account_type],
    ["Period", `${data.period_start ?? "?"} → ${data.period_end ?? "?"}`],
    ["Pages", data.page_count],
    ["Transactions", data.transaction_count],
    ["Read via", data.text_layer],
  ];

  return (
    <section>
      <SectionTitle>Extracted</SectionTitle>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-[12px]">
        {fields.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted">{label}</dt>
            <dd>{value === null || value === undefined ? "—" : String(value)}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-1.5 text-[11px] text-muted">
        The account number is masked here and everywhere else it is stored.
      </p>
    </section>
  );
}

function Metric({
  label,
  value,
  emphasise,
}: {
  label: string;
  value: number | null | undefined;
  emphasise?: boolean;
}) {
  return (
    <div className="rounded border border-line px-3 py-2">
      <p className="text-[10px] tracking-wide text-muted uppercase">{label}</p>
      <p className={emphasise ? "text-lg font-semibold" : "text-lg"}>
        {value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`}
      </p>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-1.5 text-[10px] font-medium tracking-widest text-muted uppercase">
      {children}
    </h3>
  );
}

function format(value: unknown, key?: string): string {
  if (key?.endsWith("_paise")) return rupees(value);
  if (Array.isArray(value)) return value.map((item) => format(item)).join(", ");
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  if (typeof value === "number" && !Number.isInteger(value)) return value.toFixed(3);
  return String(value);
}
