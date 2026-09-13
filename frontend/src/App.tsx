import { useEffect, useState } from "react";
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

      <main className="min-h-0 flex-1">
        {view === "customer" && <CustomerChat />}
        {view === "operations" && <Operations />}
        {view === "sales" && <SalesHandoff />}
      </main>
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
