import { useEffect, useState } from "react";
import { STATIC_DEMO } from "./lib/api";
import { CustomerChat } from "./pages/CustomerChat";
import { Operations } from "./pages/Operations";
import { SalesHandoff } from "./pages/SalesHandoff";

const VIEWS = ["customer", "operations", "sales"] as const;
type View = (typeof VIEWS)[number];

const LABELS: Record<View, string> = {
  customer: "Customer",
  operations: "Operations",
  sales: "Sales / LOS",
};

/**
 * Shell.
 *
 * A hash router in a dozen lines rather than a routing dependency: three views, no
 * nested routes, and a library would be more code to explain than to replace.
 *
 * The three tabs are the three people in the journey -- borrower, operator, lender --
 * which is also the order the case study walks through.
 */
export default function App() {
  const [view, setView] = useState<View>(currentView());

  useEffect(() => {
    const onChange = () => setView(currentView());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  return (
    <div className="flex h-full flex-col">
      <nav className="flex items-center gap-1 border-b border-line bg-white px-4 py-2">
        <span className="mr-3 text-[11px] font-medium tracking-widest text-muted uppercase">
          MSME Document Pre-Validation
        </span>
        {VIEWS.map((target) => (
          <Tab key={target} current={view} target={target} />
        ))}
      </nav>

      {STATIC_DEMO && <RecordedDemoNotice />}

      <main className="min-h-0 flex-1">
        {view === "customer" && <CustomerChat />}
        {view === "operations" && <Operations />}
        {view === "sales" && <SalesHandoff />}
      </main>
    </div>
  );
}

/**
 * Say what this is, once, where nobody can miss it.
 *
 * A demo that behaves like the product without saying it is a recording invites a
 * reasonable person to conclude something untrue. Given that this product exists to stop
 * systems from making confident claims they cannot support, leaving the label off would
 * be a poor joke.
 */
function RecordedDemoNotice() {
  return (
    <div className="border-b border-line bg-surface px-4 py-1.5 text-[11px] leading-relaxed text-muted">
      <span className="font-medium text-ink">Recorded demo.</span> There is no server
      behind this page, so the responses below are replays of real runs of the pipeline —
      every message, reason code and confidence figure is what the system actually
      produced, at the speed it actually took. Validating your own document needs the live
      backend:{" "}
      <a
        href="https://github.com/akhiii07/document-prevalidation-layer"
        target="_blank"
        rel="noreferrer"
        className="underline decoration-line underline-offset-2 hover:text-ink"
      >
        run it locally
      </a>
      .
    </div>
  );
}

function Tab({ current, target }: { current: View; target: View }) {
  const active = current === target;
  return (
    <a
      href={`#/${target}`}
      className={[
        "rounded px-2.5 py-1 text-[12px] transition",
        active ? "bg-surface font-medium text-ink" : "text-muted hover:text-ink",
      ].join(" ")}
    >
      {LABELS[target]}
    </a>
  );
}

function currentView(): View {
  const hash = window.location.hash;
  return VIEWS.find((view) => hash.includes(view)) ?? "customer";
}
