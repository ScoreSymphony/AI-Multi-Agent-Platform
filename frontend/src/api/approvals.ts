import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export interface ApprovalOwnerRef {
  type: string;
  id: string;
}

export interface CanonicalApproval {
  id: string;
  type: "approval";
  status: string;
  subject_type: string;
  subject_id: string;
  owner_ref: ApprovalOwnerRef;
  requester_ref: string;
  action: string;
  resource_type: string;
  resource_id: string;
  requested_action_digest: string;
  risk: string;
  policy_id: string;
  reason: string;
  project_id: string | null;
  task_id: string | null;
  run_id: string | null;
  capability_ref: string | null;
  payload_ref: string | null;
  created_at: string;
  expires_at: string;
  decision_by: ApprovalOwnerRef | null;
  decision_at: string | null;
  decision_comment: string | null;
}

export const APPROVAL_APPROVE_COMMAND = "approval.approve";
export const APPROVAL_DENY_COMMAND = "approval.deny";
export const APPROVAL_DECISION_COMMANDS = [
  APPROVAL_APPROVE_COMMAND,
  APPROVAL_DENY_COMMAND,
] as const;

export interface ApprovalClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

export interface ApprovalDecisionOptions {
  comment?: string;
  idempotencyKey?: string;
  correlationId?: string;
}

const APPROVAL_COLLECTION = "approvals";

export class ApprovalClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: ApprovalClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  listApprovals(query: ListQuery = {}): Promise<Page<CanonicalApproval>> {
    return this.collections.list<CanonicalApproval>(APPROVAL_COLLECTION, query);
  }

  getApproval(approvalId: string): Promise<CanonicalApproval> {
    return this.collections.get<CanonicalApproval>(
      APPROVAL_COLLECTION,
      requireNonBlank(approvalId, "Approval ID"),
    );
  }

  approve(
    approvalId: string,
    requestedActionDigest: string,
    options: ApprovalDecisionOptions = {},
  ): Promise<CanonicalApproval> {
    return this.decide(
      APPROVAL_APPROVE_COMMAND,
      approvalId,
      requestedActionDigest,
      options,
    );
  }

  deny(
    approvalId: string,
    requestedActionDigest: string,
    options: ApprovalDecisionOptions = {},
  ): Promise<CanonicalApproval> {
    return this.decide(
      APPROVAL_DENY_COMMAND,
      approvalId,
      requestedActionDigest,
      options,
    );
  }

  private async decide(
    command: typeof APPROVAL_DECISION_COMMANDS[number],
    approvalId: string,
    requestedActionDigest: string,
    options: ApprovalDecisionOptions,
  ): Promise<CanonicalApproval> {
    const resourceRef = requireNonBlank(approvalId, "Approval ID");
    const digest = requireNonBlank(requestedActionDigest, "Requested action digest");
    const idempotencyKey = requireNonBlank(
      options.idempotencyKey ?? crypto.randomUUID(),
      "Approval decision idempotency key",
    );
    const correlationId = requireNonBlank(
      options.correlationId ?? crypto.randomUUID(),
      "Approval decision correlation ID",
    );
    const body: Record<string, JsonValue> = {
      resource_ref: resourceRef,
      requested_action_digest: digest,
    };
    if (options.comment !== undefined) {
      body.comment = requireNonBlank(options.comment, "Approval decision comment");
    }

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
        body: JSON.stringify(body),
      },
    );
    const text = await response.text();
    const payload: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, payload));
    }
    return payload as CanonicalApproval;
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
