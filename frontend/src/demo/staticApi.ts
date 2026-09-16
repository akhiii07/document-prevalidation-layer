/**
 * The static build's stand-in for the backend.
 *
 * GitHub Pages serves files, not processes, so a Pages build has no pipeline behind it.
 * Rather than hand-write plausible responses, `tools/capture_demo_fixtures.py` drives the
 * real system and records what it returned and when. This module replays those
 * recordings against the clock.
 *
 * So every message, reason code, rule result, confidence figure and duration a visitor
 * sees here was genuinely produced by the product. What is simulated is only *which*
 * recording plays and when — never its content.
 *
 * The one thing it cannot do is validate a document nobody has validated yet, which is
 * why uploads are refused with an explanation rather than faked.
 */

import fixtures from "./fixtures.json";
import type {
  Application,
  ApplicationSummary,
  DocumentStatus,
  DocumentSummary,
  Handoff,
  Message,
  Requirement,
  ReviewDetail,
  ReviewSummary,
  Sample,
} from "../lib/api";

/**
 * The recording's shape, declared rather than inferred.
 *
 * Letting TypeScript infer it from the JSON produces a union of eight structurally
 * distinct tapes -- every field narrowed to the literal values that happened to be
 * captured -- which makes error messages unreadable and forces a cast at every use.
 * Naming the contract once is the whole difference.
 */
interface Frame {
  t: number;
  status: DocumentStatus;
  messages: Message[];
}

interface Tape {
  application: Application;
  welcome: Message[];
  document: DocumentSummary;
  frames: Frame[];
  awaiting_password_at: number | null;
  unlock: { password: string; frames: Frame[] } | null;
  requirement: Requirement;
  review: ReviewDetail | null;
  handoff: Handoff | null;
}

interface Fixtures {
  captured_at: string;
  samples: Sample[];
  tapes: Record<string, Tape>;
}

const DATA = fixtures as unknown as Fixtures;
const TAPES = DATA.tapes;

/**
 * Slide recorded timestamps forward to now.
 *
 * Only the timestamps, and only by one constant offset, so every interval between events
 * is preserved exactly. Without it the queue tells a visitor the oldest case has been
 * waiting since the day of capture -- which will read as "4d", then "3w", then "8mo",
 * making a working demo look abandoned. The spacing is the honest part; the absolute
 * date was never meaningful.
 */
const CAPTURE_OFFSET_MS = Date.now() - new Date(DATA.captured_at).getTime();

function freshen<T>(value: T): T {
  if (typeof value === "string") {
    return (/^\d{4}-\d{2}-\d{2}T/.test(value)
      ? new Date(new Date(value).getTime() + CAPTURE_OFFSET_MS).toISOString()
      : value) as T;
  }
  if (Array.isArray(value)) return value.map(freshen) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, freshen(v)]),
    ) as T;
  }
  return value;
}

export class DemoUnsupported extends Error {}

/** A submission made during this visit. Everything else is read from its tape. */
interface Submission {
  id: string;
  sampleId: string;
  index: number;
  startedAt: number;
  unlockedAt: number | null;
  attemptsLeft: number;
  resolvedReview: "ACCEPT" | "REQUEST_NEW_DOCUMENT" | null;
}

interface Session {
  id: string;
  reference: string;
  businessName: string;
  borrowerName: string;
  createdAt: string;
  submissions: Submission[];
}

const sessions = new Map<string, Session>();
/** Reviews the visitor has closed, so the queue reflects their decisions. */
const closedReviews = new Set<string>();

const clone = <T,>(value: T): T => freshen(JSON.parse(JSON.stringify(value)) as T);

/** The first capture group of a match that succeeded. It exists; the type says maybe. */
const group = (match: RegExpMatchArray): string => match[1] as string;
/** Any tape, for the welcome text before a visitor has sent anything. */
const anyTape = (): Tape => Object.values(TAPES)[0] as Tape;

const tapeOf = (sampleId: string): Tape => {
  const tape = TAPES[sampleId];
  if (!tape) throw new DemoUnsupported(`no recording for ${sampleId}`);
  return tape;
};

/**
 * Which recorded frame the clock has reached.
 *
 * A password prompt is a genuine stop: the recording pauses there until the visitor
 * supplies the password, exactly as the real product waits for the customer.
 */
function frameAt(sub: Submission, now: number): { frame: Frame; leg: "main" | "unlock" } {
  const tape = tapeOf(sub.sampleId);
  const pick = (frames: Frame[], elapsed: number): Frame => {
    // The capture always writes a frame for the submission itself, so index 0 exists.
    let current = frames[0] as Frame;
    for (const candidate of frames) {
      if (candidate.t > elapsed) break;
      current = candidate;
    }
    return current;
  };

  if (sub.unlockedAt !== null && tape.unlock) {
    return { frame: pick(tape.unlock.frames, now - sub.unlockedAt), leg: "unlock" };
  }
  return { frame: pick(tape.frames, now - sub.startedAt), leg: "main" };
}

/** Messages are re-keyed per submission: sending the same sample twice would otherwise
 *  reuse the recording's ids and give React duplicate keys. */
function messagesFor(sub: Submission, now: number): Message[] {
  const tape = tapeOf(sub.sampleId);
  const out: Message[] = [];
  const take = (frames: Frame[], origin: number) => {
    for (const frame of frames) {
      if (frame.t > now - origin) break;
      for (const message of frame.messages) {
        out.push({ ...clone(message), id: `${sub.id}:${message.id}` });
      }
    }
  };

  take(tape.frames, sub.startedAt);
  if (sub.unlockedAt !== null && tape.unlock) {
    take(tape.unlock.frames, sub.unlockedAt);
  }
  return out;
}

function summaryOf(sub: Submission): DocumentSummary {
  const tape = tapeOf(sub.sampleId);
  const { frame } = frameAt(sub, Date.now());
  return {
    ...clone(tape.document),
    id: sub.id,
    submission_index: sub.index,
    status: frame.status.status,
  };
}

function requirementOf(session: Session) {
  const last = session.submissions.at(-1);
  const base: Requirement = last
    ? clone(tapeOf(last.sampleId).requirement)
    : { ...clone(anyTape().requirement), status: "PENDING", submission_count: 0, first_time_cleared: null };
  const cleared = session.submissions.find(
    (s) => frameAt(s, Date.now()).frame.status.status === "PASSED",
  );
  return {
    ...base,
    status: cleared
      ? "CLEARED"
      : last
        ? base.status
        : "PENDING",
    submission_count: session.submissions.length,
    first_time_cleared: cleared ? cleared.index === 1 : null,
  };
}

function applicationOf(session: Session): Application {
  return {
    id: session.id,
    external_reference: session.reference,
    borrower_name: session.borrowerName,
    business_name: session.businessName,
    phone_number: "+9198200XXXXX",
    status: "IN_PROGRESS",
    created_at: session.createdAt,
    requirements: [requirementOf(session)],
  };
}

// ------------------------------------------------------------------ the pre-seeded work
//
// A visitor who opens Operations first must not find an empty queue and conclude the
// product is broken. These are the recorded REVIEW cases, present from the start.

function seededReviews(): ReviewSummary[] {
  return Object.entries(TAPES)
    .filter(([, tape]) => tape.review)
    .map(([, tape]) => {
      const review = tape.review as ReviewDetail;
      return {
        id: review.id,
        application_reference: review.application_reference,
        business_name: review.business_name,
        reason_code: review.reason_code,
        created_at: review.created_at,
      } as ReviewSummary;
    })
    .filter((r) => !closedReviews.has(r.id));
}

function reviewsForSession(): ReviewSummary[] {
  const live: ReviewSummary[] = [];
  for (const session of sessions.values()) {
    for (const sub of session.submissions) {
      const tape = tapeOf(sub.sampleId);
      if (!tape.review) continue;
      if (frameAt(sub, Date.now()).frame.status.status !== "IN_REVIEW") continue;
      const id = `${sub.id}:review`;
      if (closedReviews.has(id) || sub.resolvedReview) continue;
      const review = tape.review;
      live.push({
        ...clone(review),
        id,
        application_reference: session.reference,
      } as ReviewSummary);
    }
  }
  return live;
}

function findReview(id: string): ReviewDetail | null {
  for (const session of sessions.values()) {
    for (const sub of session.submissions) {
      if (`${sub.id}:review` !== id) continue;
      const tape = tapeOf(sub.sampleId);
      if (!tape.review) return null;
      return {
        ...clone(tape.review),
        id,
        application_reference: session.reference,
      };
    }
  }
  const seeded = Object.values(TAPES).find(
    (tape) => tape.review?.id === id,
  );
  return seeded?.review ? clone(seeded.review) : null;
}

function seededApplications(): ApplicationSummary[] {
  return Object.entries(TAPES).map(([sampleId, tape]) => {
    const app = tape.application;
    const req = tape.requirement;
    return {
      id: `seed:${sampleId}`,
      external_reference: app.external_reference,
      borrower_name: app.borrower_name,
      business_name: app.business_name,
      status: "IN_PROGRESS",
      created_at: app.created_at,
      requirement_status: req.status,
      submission_count: req.submission_count,
      first_time_cleared: req.first_time_cleared,
      verified_documents: req.status === "CLEARED" ? 1 : 0,
    } as ApplicationSummary;
  });
}

// ------------------------------------------------------------------ the router

export function handleDemoRequest(path: string, init?: RequestInit): unknown {
  const method = (init?.method ?? "GET").toUpperCase();
  const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
  const now = Date.now();

  if (path === "/demo/samples") return clone(DATA.samples);

  if (path === "/applications" && method === "POST") {
    const session: Session = {
      id: `demo-${Math.random().toString(36).slice(2, 10)}`,
      reference: body.external_reference,
      businessName: body.business_name,
      borrowerName: body.borrower_name,
      createdAt: new Date().toISOString(),
      submissions: [],
    };
    sessions.set(session.id, session);
    return applicationOf(session);
  }

  if (path === "/applications" && method === "GET") {
    const live = [...sessions.values()]
      .filter((s) => s.submissions.length)
      .map((s) => {
        const req = requirementOf(s);
        return {
          id: s.id,
          external_reference: s.reference,
          borrower_name: s.borrowerName,
          business_name: s.businessName,
          status: "IN_PROGRESS",
          created_at: s.createdAt,
          requirement_status: req.status,
          submission_count: req.submission_count,
          first_time_cleared: req.first_time_cleared,
          verified_documents: req.status === "CLEARED" ? 1 : 0,
        } as ApplicationSummary;
      });
    return [...live, ...seededApplications()];
  }

  if (path === "/reviews" && method === "GET") {
    return [...reviewsForSession(), ...seededReviews()];
  }

  const reviewDecision = path.match(/^\/reviews\/(.+)\/decision$/);
  if (reviewDecision && method === "POST") {
    const review = findReview(group(reviewDecision));
    if (!review) throw new DemoUnsupported("review not found");
    closedReviews.add(group(reviewDecision));
    return { ...review, status: "RESOLVED", action: body?.action ?? "ACCEPT" };
  }

  const reviewDetail = path.match(/^\/reviews\/(.+)$/);
  if (reviewDetail && method === "GET") {
    const review = findReview(group(reviewDetail));
    if (!review) throw new DemoUnsupported("review not found");
    return review;
  }

  const handoff = path.match(/^\/applications\/(.+)\/handoff$/);
  if (handoff) {
    const session = sessions.get(group(handoff));
    if (session) {
      const passed = session.submissions.find(
        (s) => frameAt(s, now).frame.status.status === "PASSED",
      );
      const tape = passed ? tapeOf(passed.sampleId) : null;
      if (!tape?.handoff) throw new DemoUnsupported("nothing verified yet");
      return { ...clone(tape.handoff), application_reference: session.reference };
    }
    const sampleId = group(handoff).replace(/^seed:/, "");
    const tape = TAPES[sampleId];
    if (!tape?.handoff) throw new DemoUnsupported("nothing verified yet");
    return clone(tape.handoff);
  }

  const sample = path.match(/^\/applications\/(.+)\/documents\/sample$/);
  if (sample && method === "POST") {
    const session = sessions.get(group(sample));
    if (!session) throw new DemoUnsupported("application not found");
    tapeOf(body.id);
    const sub: Submission = {
      id: `${session.id}-d${session.submissions.length + 1}`,
      sampleId: body.id,
      index: session.submissions.length + 1,
      startedAt: now,
      unlockedAt: null,
      attemptsLeft: 3,
      resolvedReview: null,
    };
    session.submissions.push(sub);
    return summaryOf(sub);
  }

  const upload = path.match(/^\/applications\/(.+)\/documents$/);
  if (upload && method === "POST") {
    throw new DemoUnsupported(
      "This is a recorded demo on GitHub Pages, so there is no server to read a new " +
        "document. Pick one of the sample documents below — each plays back a real run " +
        "of the pipeline. To validate your own file, run the project locally.",
    );
  }
  if (upload && method === "GET") {
    const session = sessions.get(group(upload));
    return session ? session.submissions.map(summaryOf) : [];
  }

  const messages = path.match(/^\/applications\/(.+)\/messages$/);
  if (messages) {
    const session = sessions.get(group(messages));
    if (!session) return [];
    const first = session.submissions[0];
    const welcome = clone(
      (first ? tapeOf(first.sampleId) : anyTape()).welcome,
    );
    return [...welcome, ...session.submissions.flatMap((s) => messagesFor(s, now))];
  }

  const password = path.match(/^\/documents\/(.+)\/password$/);
  if (password && method === "POST") {
    for (const session of sessions.values()) {
      const sub = session.submissions.find((s) => s.id === group(password));
      if (!sub) continue;
      const tape = tapeOf(sub.sampleId);
      if (tape.unlock && body?.password === tape.unlock.password) {
        sub.unlockedAt = Date.now();
        return { status: "accepted" };
      }
      sub.attemptsLeft = Math.max(0, sub.attemptsLeft - 1);
      return { status: "rejected" };
    }
    throw new DemoUnsupported("document not found");
  }

  const status = path.match(/^\/documents\/(.+)\/status$/);
  if (status) {
    for (const session of sessions.values()) {
      const sub = session.submissions.find((s) => s.id === group(status));
      if (!sub) continue;
      const { frame } = frameAt(sub, now);
      return {
        ...clone(frame.status),
        id: sub.id,
        password_attempts_remaining: frame.status.awaiting_password ? sub.attemptsLeft : null,
      };
    }
    throw new DemoUnsupported("document not found");
  }

  const application = path.match(/^\/applications\/(.+)$/);
  if (application && method === "GET") {
    const session = sessions.get(group(application));
    if (!session) throw new DemoUnsupported("application not found");
    return applicationOf(session);
  }

  if (path === "/health") return { status: "ok" };

  throw new DemoUnsupported(`no recording for ${method} ${path}`);
}
