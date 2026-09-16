/**
 * Backend client.
 *
 * Requests go to a same-origin `/api` prefix, which the Vite dev server proxies to the
 * backend. In a deployed build the same prefix is served by the API itself, so the
 * frontend never carries a backend hostname or any secret.
 */

import { opsHeaders } from "./ops";

const BASE = "/api";

/**
 * The static build has no backend.
 *
 * Set at build time, never at runtime, so a deployment that *does* have a backend cannot
 * be switched into replaying recordings by anything a visitor does.
 */
export const STATIC_DEMO = import.meta.env.VITE_STATIC_DEMO === "1";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (STATIC_DEMO) return requestFromRecording<T>(path, init);

  // `...init` must come FIRST. Spreading it last overwrites the merged `headers`
  // object with whatever the caller passed, which silently drops `Content-Type` on any
  // request that also sets a header -- every operations call. The body then arrives
  // unparsed and FastAPI rejects it with a validation error that looks like a schema
  // problem rather than a transport one.
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      // FormData must set its own Content-Type so the browser can add the boundary.
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    let detail: string | undefined;
    try {
      detail = describeDetail((await response.json())?.detail);
    } catch {
      detail = undefined;
    }
    throw new ApiError(detail ?? `${init?.method ?? "GET"} ${path} failed`, response.status, detail);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

/**
 * Serve a call from the recorded pipeline instead of the network.
 *
 * Deliberately routed through the same `request` signature the live client uses, so no
 * page, hook or component knows which mode it is running in — the difference lives in
 * exactly one place. The artificial delay is not decoration: without it, state
 * transitions land in the same frame as the request and the processing indicator never
 * appears, which would misrepresent how the product behaves.
 */
async function requestFromRecording<T>(path: string, init?: RequestInit): Promise<T> {
  const { handleDemoRequest, DemoUnsupported } = await import("../demo/staticApi");
  await new Promise((resolve) => setTimeout(resolve, 90 + Math.random() * 120));
  try {
    return handleDemoRequest(path, init) as T;
  } catch (error) {
    if (error instanceof DemoUnsupported) {
      throw new ApiError(error.message, 501, error.message);
    }
    throw error;
  }
}

/**
 * Turn FastAPI's `detail` into something a person can read.
 *
 * It is a plain string for a raised `HTTPException` but an **array of objects** for a
 * request-validation failure. Treating it as a string in both cases renders
 * "[object Object]" on screen — which is exactly what happened, and it hid a real
 * backend error behind a meaningless banner for several minutes.
 */
function describeDetail(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((entry) => {
        if (typeof entry === "string") return entry;
        const record = entry as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(record.loc) ? record.loc.slice(1).join(".") : "";
        return field ? `${field}: ${record.msg ?? "invalid"}` : (record.msg ?? "invalid");
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return undefined;
}

// --------------------------------------------------------------------- types

export type Outcome = "PASS" | "FIX" | "REVIEW";

export interface Requirement {
  id: string;
  document_type: string;
  status: string;
  submission_count: number;
  first_time_cleared: boolean | null;
  cleared_at: string | null;
}

export interface Application {
  id: string;
  external_reference: string;
  borrower_name: string;
  business_name: string;
  phone_number: string;
  status: string;
  created_at: string;
  requirements: Requirement[];
}

export interface DocumentSummary {
  id: string;
  requirement_id: string;
  submission_index: number;
  status: string;
  original_filename: string;
  file_size: number;
  is_encrypted: boolean;
  created_at: string;
}

export interface DocumentStatus {
  id: string;
  status: string;
  outcome: Outcome | null;
  reason_code: string | null;
  customer_message: string | null;
  awaiting_password: boolean;
  password_attempts_remaining: number | null;
}

export interface Message {
  id: string;
  direction: "INBOUND" | "OUTBOUND";
  kind: string;
  body: string;
  context: Record<string, unknown> | null;
  document_id: string | null;
  created_at: string;
}

export interface ApplicationSummary {
  id: string;
  external_reference: string;
  borrower_name: string;
  business_name: string;
  status: string;
  created_at: string;
  requirement_status: string | null;
  submission_count: number;
  first_time_cleared: boolean | null;
  verified_documents: number;
}

export interface RuleResult {
  rule_id: string;
  status: "PASS" | "FAIL" | "NOT_EVALUATED" | "SIGNAL";
  reason_code: string | null;
  blocking: boolean;
  evidence: Record<string, unknown>;
  evidence_confidence: number;
}

export interface ReviewSummary {
  id: string;
  document_id: string;
  application_reference: string;
  borrower_name: string;
  business_name: string;
  reason_code: string;
  confidence: number | null;
  submission_index: number;
  original_filename: string;
  created_at: string;
}

export interface ReviewDetail extends ReviewSummary {
  status: string;
  document_status: string;
  rule_results: RuleResult[] | null;
  confidence_breakdown: {
    composite: number | null;
    field_score: number | null;
    rule_coverage: number | null;
  } | null;
  extracted: Record<string, unknown> | null;
  customer_message: string | null;
  download_url: string;
}

export interface HandoffDocument {
  document_id: string;
  status: string;
  submission_index: number;
  verified_at: string | null;
  bank_name: string | null;
  account_holder_name: string | null;
  account_number_masked: string | null;
  account_type: string | null;
  period_start: string | null;
  period_end: string | null;
  page_count: number | null;
  transaction_count: number | null;
  validation: {
    outcome?: string;
    checks?: Record<string, string>;
    confidence?: number | null;
    rules_version?: number | null;
    manual_review?: {
      reason_code: string;
      action: string;
      reviewer_id: string | null;
      note: string | null;
      resolved_at: string | null;
    };
  };
  download_url: string;
}

export interface Handoff {
  application_reference: string;
  borrower_name: string;
  business_name: string;
  requirement_status: string;
  first_time_cleared: boolean | null;
  submission_count: number;
  documents: HandoffDocument[];
}

export interface Sample {
  id: string;
  file: string;
  scenario: string;
  expected_outcome: Outcome;
  expected_reason_code: string | null;
  password: string | null;
  notes: string;
}

/** Statuses where the system is still working and the client should keep polling. */
export const WORKING_STATUSES = new Set([
  "RECEIVED",
  "INGESTING",
  "EXTRACTING",
  "EXTRACTED",
  "VALIDATING",
]);

export const TERMINAL_STATUSES = new Set(["PASSED", "NEEDS_FIX", "SUPERSEDED"]);

/**
 * The system has finished and is waiting on a person.
 *
 * Not terminal -- a reviewer may still resolve it -- but the customer should not be
 * shown a spinner, because nothing is being computed on their behalf any more.
 */
export const WAITING_ON_HUMAN = new Set(["IN_REVIEW"]);

// --------------------------------------------------------------------- calls

export const api = {
  createApplication: (payload: {
    external_reference: string;
    borrower_name: string;
    business_name: string;
    phone_number: string;
  }) =>
    request<Application>("/applications", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getApplication: (id: string) => request<Application>(`/applications/${id}`),

  documents: (applicationId: string) =>
    request<DocumentSummary[]>(`/applications/${applicationId}/documents`),

  messages: (applicationId: string) =>
    request<Message[]>(`/applications/${applicationId}/messages`),

  uploadDocument: (applicationId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentSummary>(`/applications/${applicationId}/documents`, {
      method: "POST",
      body: form,
    });
  },

  submitSample: (applicationId: string, sampleId: string) =>
    request<DocumentSummary>(`/applications/${applicationId}/documents/sample`, {
      method: "POST",
      body: JSON.stringify({ id: sampleId }),
    }),

  documentStatus: (documentId: string) =>
    request<DocumentStatus>(`/documents/${documentId}/status`),

  submitPassword: (documentId: string, password: string) =>
    request<{ status: string }>(`/documents/${documentId}/password`, {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  samples: () => request<Sample[]>("/demo/samples"),

  // --- operations (all require the operator credential) --------------------
  listApplications: () =>
    request<ApplicationSummary[]>("/applications", { headers: opsHeaders() }),

  reviews: () => request<ReviewSummary[]>("/reviews", { headers: opsHeaders() }),

  review: (id: string) => request<ReviewDetail>(`/reviews/${id}`, { headers: opsHeaders() }),

  decide: (id: string, action: "ACCEPT" | "REQUEST_NEW_DOCUMENT", reviewerId: string, note: string) =>
    request<ReviewDetail>(`/reviews/${id}/decision`, {
      method: "POST",
      headers: opsHeaders(),
      body: JSON.stringify({ action, reviewer_id: reviewerId || null, note: note || null }),
    }),

  handoff: (applicationId: string) =>
    request<Handoff>(`/applications/${applicationId}/handoff`, { headers: opsHeaders() }),
};
