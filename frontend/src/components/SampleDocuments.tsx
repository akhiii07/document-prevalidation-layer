import { useEffect, useState } from "react";
import { api, type Sample } from "../lib/api";

const OUTCOME_STYLES: Record<string, string> = {
  PASS: "bg-pass/10 text-pass",
  FIX: "bg-fix/10 text-fix",
  REVIEW: "bg-review/10 text-review",
};

/**
 * Demo affordance: send a corpus document as though the customer had.
 *
 * It posts the document id, and the backend runs it through the same `ingest()` path as
 * a real upload — same file checks, same queue, same worker. A shortcut that bypassed
 * the pipeline would demonstrate the wrong thing.
 */
export function SampleDocuments({
  onSend,
  busy,
}: {
  onSend: (sample: Sample) => void;
  busy: boolean;
}) {
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api
      .samples()
      .then(setSamples)
      .catch(() => setSamples([]));
  }, []);

  if (!samples || samples.length === 0) return null;

  const grouped = samples.reduce<Record<string, Sample[]>>((acc, sample) => {
    const key = sample.scenario.replace(/^\d+_/, "").replace(/_/g, " ");
    (acc[key] ??= []).push(sample);
    return acc;
  }, {});

  return (
    <div className="border-t border-line bg-surface">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between px-4 py-2 text-[11px] font-medium tracking-wide text-muted uppercase"
      >
        Synthetic test documents
        <span className="text-[14px] leading-none">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="max-h-56 space-y-3 overflow-y-auto px-4 pb-3">
          {Object.entries(grouped).map(([scenario, items]) => (
            <div key={scenario}>
              <p className="mb-1 text-[10px] tracking-wide text-muted uppercase">{scenario}</p>
              <div className="flex flex-wrap gap-1.5">
                {items.map((sample) => (
                  <button
                    key={sample.id}
                    type="button"
                    disabled={busy}
                    title={sample.notes}
                    onClick={() => onSend(sample)}
                    className="flex items-center gap-1.5 rounded-full border border-line bg-white px-2.5 py-1 text-[11px] transition hover:border-accent disabled:opacity-40"
                  >
                    <span
                      className={`rounded px-1 py-px text-[9px] font-semibold ${
                        OUTCOME_STYLES[sample.expected_outcome] ?? ""
                      }`}
                    >
                      {sample.expected_outcome}
                    </span>
                    {sample.id}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
