import type { Message } from "../lib/api";

/**
 * One message in the thread.
 *
 * Body text is rendered as the backend composed it — the UI adds styling, never
 * content. Lines beginning with ✓ or ❌ are lifted into a checklist row, which is
 * presentation only: the backend decides what the customer is told, and the frontend
 * must not be able to change that (`PRODUCT_SPEC.md` §9).
 */
export function MessageBubble({
  message,
  showAttribution = false,
}: {
  message: Message;
  showAttribution?: boolean;
}) {
  const fromCustomer = message.direction === "INBOUND";

  if (message.kind === "DOCUMENT_RECEIVED") {
    return <DocumentBubble message={message} />;
  }

  const filename =
    typeof message.context?.filename === "string" ? message.context.filename : null;

  return (
    <div className={`flex ${fromCustomer ? "justify-end" : "justify-start"}`}>
      <div
        className={[
          "max-w-[85%] rounded-lg px-3 py-2 text-[13px] leading-relaxed shadow-sm",
          fromCustomer ? "bg-bubble-out text-ink" : "bg-white text-ink",
        ].join(" ")}
      >
        {/*
          Which document this verdict is about.
          Shown only once a thread holds more than one, because on a single-document
          thread it is noise — but the moment there are two, its absence is how a
          customer reads one document's verdict as a judgement on another.
        */}
        {showAttribution && filename && (
          <p className="mb-1 truncate border-l-2 border-line pl-2 text-[11px] text-muted">
            {filename}
          </p>
        )}
        {renderBody(message.body)}
        <Timestamp at={message.created_at} />
      </div>
    </div>
  );
}

function DocumentBubble({ message }: { message: Message }) {
  const size = Number(message.context?.size_bytes ?? 0);
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-lg bg-bubble-out px-3 py-2 shadow-sm">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-white/70">
            <span className="text-[10px] font-bold tracking-tight text-fix">PDF</span>
          </div>
          <div className="min-w-0">
            <p className="truncate text-[13px] font-medium">{message.body}</p>
            <p className="text-[11px] text-muted">
              {size > 0 ? `${(size / 1024).toFixed(0)} KB` : "document"}
            </p>
          </div>
        </div>
        <Timestamp at={message.created_at} />
      </div>
    </div>
  );
}

function renderBody(body: string) {
  const lines = body.split("\n");
  return (
    <div className="space-y-0.5">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        if (trimmed === "") return <div key={index} className="h-2" />;

        if (trimmed.startsWith("✓") || trimmed.startsWith("❌")) {
          const passed = trimmed.startsWith("✓");
          return (
            <div key={index} className="flex items-start gap-1.5">
              <span className={passed ? "text-pass" : "text-fix"}>{passed ? "✓" : "✕"}</span>
              <span className={passed ? "" : "font-medium"}>{trimmed.slice(1).trim()}</span>
            </div>
          );
        }
        return (
          <p key={index} className="whitespace-pre-wrap">
            {line}
          </p>
        );
      })}
    </div>
  );
}

function Timestamp({ at }: { at: string }) {
  const time = new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return <p className="mt-1 text-right text-[10px] text-muted">{time}</p>;
}
