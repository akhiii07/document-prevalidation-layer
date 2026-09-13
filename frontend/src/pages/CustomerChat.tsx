import { useCallback, useEffect, useRef, useState } from "react";
import { Composer } from "../components/Composer";
import { MessageBubble } from "../components/MessageBubble";
import { ProcessingIndicator } from "../components/ProcessingIndicator";
import { SampleDocuments } from "../components/SampleDocuments";
import {
  api,
  TERMINAL_STATUSES,
  WAITING_ON_HUMAN,
  type Application,
  type DocumentStatus,
  type Message,
  type Sample,
} from "../lib/api";

const STORAGE_KEY = "docverify.applicationId";
const POLL_MS = 1200;
// A document with Operations is no longer moving on its own; back off rather than
// hammering the API while a human decides.
const REVIEW_POLL_MS = 5000;

/** What the customer sees while the document is in flight. */
const STAGE_LABELS: Record<string, string> = {
  RECEIVED: "Received your document",
  INGESTING: "Checking the file",
  EXTRACTING: "Reading your statement",
  EXTRACTED: "Reading your statement",
  VALIDATING: "Verifying the details",
};

/**
 * Waiting is a stage too.
 *
 * A scanned or photographed statement has to be read page by page, which takes tens of
 * seconds — and a spinner that says nothing new for ninety of them is indistinguishable
 * from one that has crashed. Rather than pretend the wait isn't happening, say what is
 * taking the time and roughly how much longer it will be. The numbers below are the
 * measured OCR cost, not decoration.
 */
const WAIT_HINTS: { after: number; hint: string }[] = [
  {
    after: 12,
    hint: "This looks like a scan or a photo, so I'm reading it page by page. Usually about a minute.",
  },
  { after: 50, hint: "Still reading — a few more pages to go." },
  {
    after: 120,
    hint: "This one is taking longer than usual. I'll have an answer shortly; you don't need to send it again.",
  },
];

function waitHint(seconds: number): string | null {
  let hint: string | null = null;
  for (const entry of WAIT_HINTS) {
    if (seconds >= entry.after) hint = entry.hint;
  }
  return hint;
}

export function CustomerChat() {
  const [application, setApplication] = useState<Application | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [active, setActive] = useState<DocumentStatus | null>(null);
  const [busy, setBusy] = useState(false);
  // Set when a password has been sent and the worker has not yet acted on it.
  // Without this, polling stops the instant the status reads AWAITING_PASSWORD again --
  // which is the state the document is still in until the worker picks the attempt up --
  // and the customer is left frozen on a stale attempt count, never seeing the result.
  const [attemptPending, setAttemptPending] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Seconds the current submission has been in flight, ticked locally. Derived from the
  // server's timestamps rather than a mount time so a reload mid-read resumes honestly
  // instead of restarting the customer's wait at zero.
  const [waited, setWaited] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);

  // --- session -----------------------------------------------------------
  useEffect(() => {
    const existing = localStorage.getItem(STORAGE_KEY);
    const load = existing
      ? api.getApplication(existing).catch(() => createApplication())
      : createApplication();

    load
      .then((app) => {
        localStorage.setItem(STORAGE_KEY, app.id);
        setApplication(app);
      })
      .catch((e: unknown) => setError(describeError(e)));
  }, []);

  const refreshMessages = useCallback(async (applicationId: string) => {
    setMessages(await api.messages(applicationId));
  }, []);

  useEffect(() => {
    if (application) void refreshMessages(application.id);
  }, [application, refreshMessages]);

  // Resume the live submission after a reload.
  //
  // Without this the page forgets which document it is waiting on, so a customer who
  // closes the chat while a password prompt is open comes back to a thread that asked
  // them for something and then offers no way to answer. The conversation is server
  // state; the client should recover it, not assume it never happened.
  useEffect(() => {
    if (!application || active) return;
    let cancelled = false;

    void (async () => {
      const documents = await api.documents(application.id);
      const latest = documents
        .filter((document) => document.status !== "SUPERSEDED")
        .at(-1);
      if (!latest || TERMINAL_STATUSES.has(latest.status)) return;
      const status = await api.documentStatus(latest.id);
      if (!cancelled) setActive(status);
    })().catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [application, active]);

  // --- polling -----------------------------------------------------------
  // Polling rather than a socket: the pipeline takes seconds, the payload is tiny, and
  // a socket would add a connection lifecycle to maintain for no user-visible gain
  // (`MVP_Product_Requirements_and_Build_Plan.md` §22).
  useEffect(() => {
    if (!application || !active) return;
    if (TERMINAL_STATUSES.has(active.status)) return;
    // Keep polling while a password attempt is in flight, even though the document is
    // still nominally awaiting one.
    if (active.awaiting_password && attemptPending === null) return;

    const timer = setTimeout(async () => {
      try {
        const next = await api.documentStatus(active.id);
        setActive(next);
        await refreshMessages(application.id);

        const attemptResolved =
          !next.awaiting_password || next.password_attempts_remaining !== attemptPending;
        if (attemptPending !== null && attemptResolved) {
          setAttemptPending(null);
        }

        // Refresh the application on every status change, not only at the end: the
        // requirement strip is how the customer sees where they stand, and leaving it
        // on a stale PENDING while the thread says otherwise is worse than not
        // showing it at all.
        if (next.status !== active.status) {
          setApplication(await api.getApplication(application.id));
        }
      } catch {
        // A failed poll is not worth surfacing: the next tick retries, and an error
        // banner that flickers on a dropped request is worse than the dropped request.
      }
    }, WAITING_ON_HUMAN.has(active.status) ? REVIEW_POLL_MS : POLL_MS);

    return () => clearTimeout(timer);
  }, [application, active, attemptPending, refreshMessages]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, active]);

  // --- the wait ----------------------------------------------------------
  // Measured from the server's record of when the document arrived, not from when this
  // component mounted, so a reload does not reset the customer's wait to zero and start
  // telling them it will take a minute all over again.
  const startedAt = active
    ? messages.find((message) => message.document_id === active.id)?.created_at
    : undefined;

  useEffect(() => {
    if (!startedAt) {
      setWaited(0);
      return;
    }
    const from = new Date(startedAt).getTime();
    const tick = () => setWaited(Math.max(0, Math.round((Date.now() - from) / 1000)));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [startedAt]);

  // --- actions -----------------------------------------------------------
  const afterSubmit = useCallback(
    async (documentId: string, applicationId: string) => {
      setActive(await api.documentStatus(documentId));
      await refreshMessages(applicationId);
    },
    [refreshMessages],
  );

  async function handleUpload(file: File) {
    if (!application) return;
    setBusy(true);
    setError(null);
    try {
      const document = await api.uploadDocument(application.id, file);
      await afterSubmit(document.id, application.id);
    } catch (e: unknown) {
      setError(describeError(e) || "Could not send that file.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSample(sample: Sample) {
    if (!application) return;
    setBusy(true);
    setError(null);
    try {
      const document = await api.submitSample(application.id, sample.id);
      await afterSubmit(document.id, application.id);
    } catch (e: unknown) {
      setError(describeError(e) || "Could not send that document.");
    } finally {
      setBusy(false);
    }
  }

  async function handlePassword(password: string) {
    if (!application || !active) return;
    setBusy(true);
    try {
      await api.submitPassword(active.id, password);
      // Remember how many attempts were left when we sent it: polling continues until
      // that number changes or the document leaves AWAITING_PASSWORD, which is how we
      // know the worker has actually acted on this attempt.
      setAttemptPending(active.password_attempts_remaining);
      await refreshMessages(application.id);
    } catch (e: unknown) {
      setError(describeError(e) || "Could not send the password.");
    } finally {
      setBusy(false);
    }
  }

  async function restart() {
    localStorage.removeItem(STORAGE_KEY);
    setMessages([]);
    setActive(null);
    setAttemptPending(null);
    setApplication(await createApplication().then(async (app) => {
      localStorage.setItem(STORAGE_KEY, app.id);
      return app;
    }));
  }

  // --- render ------------------------------------------------------------
  // "Working" means *we* are working. A document with Operations is finished from the
  // customer's point of view, and leaving a spinner running would tell them the system
  // is still thinking when it is not.
  const working =
    active !== null &&
    !TERMINAL_STATUSES.has(active.status) &&
    !WAITING_ON_HUMAN.has(active.status) &&
    !active.awaiting_password;
  const requirement = application?.requirements[0];
  // Attribution is only worth the space once there is something to confuse it with.
  const multipleDocuments =
    new Set(messages.map((message) => message.document_id).filter(Boolean)).size > 1;

  return (
    <div className="mx-auto flex h-full max-w-[420px] flex-col overflow-hidden border-x border-line bg-chat">
      <header className="flex items-center gap-3 bg-accent px-4 py-2.5 text-white">
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-white/20 text-[13px] font-semibold">
          DV
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[14px] font-medium">Document Verification</p>
          <p className="truncate text-[11px] text-white/70">
            {working ? "typing…" : "online"}
            {application ? ` · ${application.external_reference}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={restart}
          title="Start a new application"
          className="rounded px-2 py-1 text-[11px] text-white/80 hover:bg-white/10"
        >
          Reset
        </button>
      </header>

      <div className="flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            showAttribution={multipleDocuments}
          />
        ))}
        {working && (
          <ProcessingIndicator
            label={STAGE_LABELS[active.status] ?? "Working"}
            hint={waitHint(waited)}
          />
        )}
        {error && (
          <div className="flex justify-start">
            <p className="rounded-lg bg-fix/10 px-3 py-2 text-[12px] text-fix">{error}</p>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {requirement && (
        <div className="border-t border-line bg-white/60 px-4 py-1.5 text-[10px] tracking-wide text-muted uppercase">
          {requirement.status.replace(/_/g, " ")}
          {requirement.submission_count > 0 && ` · ${requirement.submission_count} sent`}
          {requirement.first_time_cleared === false && " · corrected"}
        </div>
      )}

      <Composer
        onUpload={handleUpload}
        onPassword={handlePassword}
        awaitingPassword={Boolean(active?.awaiting_password)}
        attemptsRemaining={active?.password_attempts_remaining ?? null}
        busy={busy || working}
      />

      <SampleDocuments onSend={handleSample} busy={busy || working} />
    </div>
  );
}

function createApplication() {
  const reference = `FLX-${Math.floor(10000 + Math.random() * 89999)}`;
  return api.createApplication({
    external_reference: reference,
    borrower_name: "Rajesh Kumar Sharma",
    business_name: "Sharma Metal Works Private Limited",
    phone_number: "+919820000000",
  });
}


/** Never render a raw object in a banner: it tells the reader nothing. */
function describeError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string") return error;
  return "Something went wrong. Please try again.";
}
