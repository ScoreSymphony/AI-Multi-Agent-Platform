import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export type LearningCandidateStatus =
  | "proposed"
  | "evaluating"
  | "accepted"
  | "rejected"
  | "superseded"
  | "promoted";

export type LearningSourceType =
  | "user_feedback"
  | "verification"
  | "evaluation"
  | "run_failure_pattern"
  | "planning_failure_pattern"
  | "research_evidence"
  | "operator_proposal";

export interface LearningReference {
  kind: string;
  resource_id: string;
  revision: string | number | null;
  digest: string | null;
}

export interface LearningTarget {
  resource_type: string;
  resource_id: string;
  revision: number;
}

export interface LearningGatePlan {
  policy_id: string;
  policy_version: number;
  require_evaluation: boolean;
  require_verification: boolean;
  require_regression_free: boolean;
  approval_required_risks: string[];
  automatic_promotion_allowed: boolean;
  evaluation_suite_refs: string[];
  verification_policy_refs: string[];
}

export interface LearningPromotionReceipt {
  target_type: string;
  target_id: string;
  previous_revision: number;
  new_revision: number;
  canonical_ref: string;
  candidate_digest: string;
  already_applied: boolean;
}

export interface LearningCandidateHistoryEntry {
  revision: number;
  status: LearningCandidateStatus;
  content_digest: string;
  evaluation_run_ids: string[];
  verification_ids: string[];
  superseded_by: string | null;
  promotion: LearningPromotionReceipt | null;
  updated_at: string;
}

export interface LearningApprovalBinding {
  approval_id: string;
  status: string;
  requested_action_digest: string;
  risk: string;
  policy_id: string;
  payload_ref: string | null;
  created_at: string;
  expires_at: string;
  decision_at: string | null;
}

export interface CanonicalLearningCandidate {
  id: string;
  type: "learning-candidate";
  learning_candidate_id: string;
  revision: number;
  source_type: LearningSourceType;
  problem: string;
  target: LearningTarget;
  improvement_type: string;
  expected_benefit: string;
  risk: string;
  gate_plan: LearningGatePlan;
  creator_ref: string;
  source_refs: LearningReference[];
  evidence_refs: LearningReference[];
  proposed_change: Record<string, JsonValue>;
  proposed_artifact_ref: LearningReference | null;
  evaluation_run_ids: string[];
  verification_ids: string[];
  status: LearningCandidateStatus;
  project_id: string | null;
  superseded_by: string | null;
  promotion: LearningPromotionReceipt | null;
  created_at: string;
  updated_at: string;
  schema_version: string;
  content_digest: string;
  history: LearningCandidateHistoryEntry[];
  approvals: LearningApprovalBinding[];
  post_promotion_regression_status: string;
}

export interface CanonicalLearningFeedback {
  id: string;
  type: "learning-feedback";
  feedback_id: string;
  feedback_type: string;
  subject: LearningReference;
  creator_ref: string;
  comment: string | null;
  target: LearningTarget | null;
  project_id: string | null;
  created_at: string;
  schema_version: string;
  content_digest: string;
}

export interface CanonicalPostPromotionEvaluation {
  id: string;
  type: "learning-post-promotion-evaluation";
  learning_candidate_id: string;
  candidate_revision: number;
  target_revision: number;
  outcome: "passed" | "regression" | "failed" | "not_configured";
  evaluation_run_ids: string[];
  details: Record<string, JsonValue>;
  created_at: string;
}

export interface LearningCommandOptions {
  idempotencyKey?: string;
  correlationId?: string;
}

export interface LearningClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const CANDIDATE_COLLECTION = "learning-candidates";
const FEEDBACK_COLLECTION = "learning-feedback";
const POST_PROMOTION_COLLECTION = "learning-post-promotion-evaluations";

export class LearningClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: LearningClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  listCandidates(query: ListQuery = {}): Promise<Page<CanonicalLearningCandidate>> {
    return this.collections.list<CanonicalLearningCandidate>(CANDIDATE_COLLECTION, query);
  }

  getCandidate(candidateId: string): Promise<CanonicalLearningCandidate> {
    return this.collections.get<CanonicalLearningCandidate>(
      CANDIDATE_COLLECTION,
      requireNonBlank(candidateId, "Learning Candidate ID"),
    );
  }

  listFeedback(query: ListQuery = {}): Promise<Page<CanonicalLearningFeedback>> {
    return this.collections.list<CanonicalLearningFeedback>(FEEDBACK_COLLECTION, query);
  }

  getFeedback(feedbackId: string): Promise<CanonicalLearningFeedback> {
    return this.collections.get<CanonicalLearningFeedback>(
      FEEDBACK_COLLECTION,
      requireNonBlank(feedbackId, "Learning feedback ID"),
    );
  }

  listPostPromotionEvaluations(
    query: ListQuery = {},
  ): Promise<Page<CanonicalPostPromotionEvaluation>> {
    return this.collections.list<CanonicalPostPromotionEvaluation>(POST_PROMOTION_COLLECTION, query);
  }

  recordFeedback(
    payload: Record<string, JsonValue>,
    options: LearningCommandOptions = {},
  ): Promise<CanonicalLearningFeedback> {
    return this.command<CanonicalLearningFeedback>(
      "learning.feedback.create",
      FEEDBACK_COLLECTION,
      payload,
      options,
    );
  }

  propose(
    payload: Record<string, JsonValue>,
    options: LearningCommandOptions = {},
  ): Promise<CanonicalLearningCandidate> {
    return this.command<CanonicalLearningCandidate>(
      "learning.propose",
      CANDIDATE_COLLECTION,
      payload,
      options,
    );
  }

  proposeFromFeedback(
    feedbackId: string,
    payload: Record<string, JsonValue>,
    options: LearningCommandOptions = {},
  ): Promise<CanonicalLearningCandidate> {
    return this.command<CanonicalLearningCandidate>(
      "learning.propose-from-feedback",
      requireNonBlank(feedbackId, "Learning feedback ID"),
      payload,
      options,
    );
  }

  recordEvidence(
    candidateId: string,
    expectedRevision: number,
    evidence: { evaluation_run_ids?: string[]; verification_ids?: string[] },
    options: LearningCommandOptions = {},
  ): Promise<CanonicalLearningCandidate> {
    return this.command<CanonicalLearningCandidate>(
      "learning.evidence",
      requireNonBlank(candidateId, "Learning Candidate ID"),
      { expected_revision: expectedRevision, ...evidence },
      options,
    );
  }

  accept(
    candidateId: string,
    expectedRevision: number,
    options: LearningCommandOptions = {},
  ): Promise<CanonicalLearningCandidate> {
    return this.lifecycleCommand("learning.accept", candidateId, expectedRevision, {}, options);
  }

  reject(
    candidateId: string,
    expectedRevision: number,
    options: LearningCommandOptions = {},
  ): Promise<CanonicalLearningCandidate> {
    return this.lifecycleCommand("learning.reject", candidateId, expectedRevision, {}, options);
  }

  supersede(
    candidateId: string,
    expectedRevision: number,
    supersededBy: string,
    options: LearningCommandOptions = {},
  ): Promise<CanonicalLearningCandidate> {
    return this.lifecycleCommand(
      "learning.supersede",
      candidateId,
      expectedRevision,
      { superseded_by: requireNonBlank(supersededBy, "Replacement Learning Candidate ID") },
      options,
    );
  }

  promote(
    candidateId: string,
    expectedRevision: number,
    approvalId?: string,
    options: LearningCommandOptions = {},
  ): Promise<CanonicalLearningCandidate> {
    const payload: Record<string, JsonValue> = { expected_revision: expectedRevision };
    if (approvalId !== undefined) {
      payload.approval_id = requireNonBlank(approvalId, "Approval ID");
    }
    return this.command<CanonicalLearningCandidate>(
      "learning.promote",
      requireNonBlank(candidateId, "Learning Candidate ID"),
      payload,
      options,
    );
  }

  private lifecycleCommand(
    command: string,
    candidateId: string,
    expectedRevision: number,
    payload: Record<string, JsonValue>,
    options: LearningCommandOptions,
  ): Promise<CanonicalLearningCandidate> {
    return this.command<CanonicalLearningCandidate>(
      command,
      requireNonBlank(candidateId, "Learning Candidate ID"),
      { expected_revision: expectedRevision, ...payload },
      options,
    );
  }

  private async command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, JsonValue>,
    options: LearningCommandOptions,
  ): Promise<T> {
    const correlationId = requireNonBlank(
      options.correlationId ?? crypto.randomUUID(),
      "Learning command correlation ID",
    );
    const idempotencyKey = requireNonBlank(
      options.idempotencyKey ?? crypto.randomUUID(),
      "Learning command idempotency key",
    );
    const response = await this.fetchImpl(
      `${this.baseUrl}/api/v1/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Correlation-ID": correlationId,
          "Idempotency-Key": idempotencyKey,
        },
        credentials: "include",
        body: JSON.stringify({ resource_ref: resourceRef, ...payload }),
      },
    );
    const text = await response.text();
    const parsed: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, parsed));
    }
    return parsed as T;
  }
}

function requireNonBlank(value: string, label: string): string {
  if (!value.trim()) throw new Error(`${label} is required`);
  return value;
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
    typeof candidate.code === "string"
    && typeof candidate.category === "string"
    && typeof candidate.message === "string"
    && typeof candidate.request_id === "string"
    && typeof candidate.correlation_id === "string"
    && typeof candidate.retryable === "boolean"
  );
}
