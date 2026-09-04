import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export type VerificationOutcome = "pass" | "fail" | "needs_changes" | "inconclusive";
export type VerificationRequestStatus = "pending" | "completed" | "expired" | "cancelled";
export type VerificationCompletionState =
  | "accepted"
  | "waiting"
  | "repair_required"
  | "rejected"
  | "escalated";
export type VerificationReviewerKind = "deterministic" | "human" | "agent" | "provider";
export type VerificationReviewAction = "accept" | "reject" | "request-changes";

export interface VerificationSubject {
  type: "result" | "artifact";
  id: string;
  revision: string;
  digest: string;
}

export interface VerificationPolicyRef {
  id: string;
  version: number;
}

export interface VerificationProducer {
  actor_ref: string;
  agent_id: string | null;
  agent_revision: number | null;
  model_config_id: string | null;
  provider_id: string | null;
}

export interface VerificationVerifier {
  ref: string;
  kind: VerificationReviewerKind;
  agent_id: string | null;
  agent_revision: number | null;
  model_config_id: string | null;
  provider_id: string | null;
  read_only: boolean;
}

export interface VerificationFinding {
  code: string;
  message: string;
  severity: string;
  location_ref: string | null;
}

export interface VerificationErrorRecord {
  code: string;
  message: string;
  retryable: boolean;
}

export interface CanonicalVerificationResult {
  id: string;
  verification_id: string;
  outcome: VerificationOutcome;
  subject: VerificationSubject;
  verifier: VerificationVerifier;
  findings: VerificationFinding[];
  evidence_artifact_ids: string[];
  checks_executed: string[];
  errors: VerificationErrorRecord[];
  started_at: string;
  completed_at: string;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalVerification {
  id: string;
  type: "verification";
  task_id: string;
  run_id: string | null;
  result_id: string | null;
  artifact_ids: string[];
  project_id: string | null;
  capability_ids: string[];
  policy: VerificationPolicyRef;
  stage_id: string;
  subject: VerificationSubject;
  requested_verifier_kind: VerificationReviewerKind;
  requested_capability_ref: string | null;
  repair_attempt: number;
  status: VerificationRequestStatus;
  created_at: string;
  expires_at: string | null;
  correlation_id: string;
  causation_id: string | null;
  producer?: VerificationProducer;
  verification_result?: CanonicalVerificationResult;
}

export interface CanonicalVerificationRequirement {
  id: string;
  type: "verification_requirement";
  task_id: string;
  policy: VerificationPolicyRef;
  subject: VerificationSubject | null;
  created_at: string;
  updated_at: string;
  completion: {
    state: VerificationCompletionState;
    reason: string;
    blocking_verification_ids: string[];
    repair_attempts_remaining: number;
  };
}

export interface HumanReviewInput {
  comment?: string;
  evidence_artifact_ids?: string[];
}

export interface VerificationClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const VERIFICATIONS = "verifications";
const REVIEWS = "verification-reviews";
const REQUIREMENTS = "verification-requirements";

export class VerificationClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: VerificationClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  list(query: ListQuery = {}): Promise<Page<CanonicalVerification>> {
    return this.collections.list<CanonicalVerification>(VERIFICATIONS, query);
  }

  get(verificationId: string): Promise<CanonicalVerification> {
    return this.collections.get<CanonicalVerification>(VERIFICATIONS, verificationId);
  }

  listPendingReviews(query: ListQuery = {}): Promise<Page<CanonicalVerification>> {
    return this.collections.list<CanonicalVerification>(REVIEWS, query);
  }

  getPendingReview(verificationId: string): Promise<CanonicalVerification> {
    return this.collections.get<CanonicalVerification>(REVIEWS, verificationId);
  }

  listRequirements(query: ListQuery = {}): Promise<Page<CanonicalVerificationRequirement>> {
    return this.collections.list<CanonicalVerificationRequirement>(REQUIREMENTS, query);
  }

  getRequirement(taskId: string): Promise<CanonicalVerificationRequirement> {
    return this.collections.get<CanonicalVerificationRequirement>(REQUIREMENTS, taskId);
  }

  accept(
    verificationId: string,
    input: HumanReviewInput = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalVerification> {
    return this.review("verification.accept", verificationId, input, idempotencyKey);
  }

  reject(
    verificationId: string,
    input: HumanReviewInput = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalVerification> {
    return this.review("verification.reject", verificationId, input, idempotencyKey);
  }

  requestChanges(
    verificationId: string,
    input: HumanReviewInput = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalVerification> {
    return this.review("verification.request-changes", verificationId, input, idempotencyKey);
  }

  private async review(
    command: string,
    verificationId: string,
    input: HumanReviewInput,
    idempotencyKey: string,
  ): Promise<CanonicalVerification> {
    if (!idempotencyKey.trim()) throw new Error("verification review idempotency key is required");
    const headers = new Headers({
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
      "Idempotency-Key": idempotencyKey,
    });
    const payload: Record<string, JsonValue> = { resource_ref: verificationId };
    const comment = input.comment?.trim();
    if (comment) payload.comment = comment;
    if (input.evidence_artifact_ids?.length) {
      payload.evidence_artifact_ids = input.evidence_artifact_ids;
    }
    const response = await this.fetchImpl(
      `${this.baseUrl}/api/v1/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify(payload),
      },
    );
    const text = await response.text();
    const body: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, body));
    }
    return body as CanonicalVerification;
  }
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function normalizeError(response: Response, payload: unknown): APIErrorBody {
  if (isErrorBody(payload)) return payload;
  const requestId = response.headers.get("x-request-id") ?? "unknown";
  return {
    code: "invalid_response",
    category: "contract",
    message: `Control Plane returned HTTP ${response.status} without a canonical error envelope`,
    request_id: requestId,
    correlation_id: response.headers.get("x-correlation-id") ?? requestId,
    retryable: false,
  };
}

function isErrorBody(value: unknown): value is APIErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<APIErrorBody>;
  return (
    typeof candidate.code === "string" &&
    typeof candidate.category === "string" &&
    typeof candidate.message === "string" &&
    typeof candidate.request_id === "string" &&
    typeof candidate.correlation_id === "string" &&
    typeof candidate.retryable === "boolean"
  );
}
