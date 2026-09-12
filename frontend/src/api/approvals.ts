import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

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

export interface ApprovalClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export interface ApprovalDecisionOptions {
  comment?: string;
  idempotencyKey?: string;
  correlationId?: string;
}

const APPROVAL_COLLECTION = "approvals";

export class ApprovalClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: ApprovalClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
    this.collections = new ControlPlaneCollectionClient({ transport: this.transport });
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

  private decide(
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

    return this.transport.request<CanonicalApproval>(
      `/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        headers: { "X-Correlation-ID": correlationId },
        idempotencyKey,
        body,
      },
    );
  }
}

function requireNonBlank(value: string, label: string): string {
  if (!value.trim()) throw new Error(`${label} is required`);
  return value;
}
