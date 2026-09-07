import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, ListQuery, Page } from "./types";

export interface GovernanceClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

export interface GovernanceProposal {
  id: string;
  type: "proposal";
  title: string;
  summary: string;
  reason: string;
  status: string;
  revision: number;
  project_id: string | null;
  workspace_id: string | null;
  requester_ref: string;
  source: string;
  evidence_refs: string[];
  confidence: number | null;
  expected_value: number | null;
  risk: string;
  fingerprint: string | null;
  supersedes_id: string | null;
  superseded_by_id: string | null;
  converted_task_id: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface GovernanceSpecification {
  id: string;
  type: "specification" | "specification-revision";
  title: string;
  summary: string;
  revision: number;
  version: string;
  content_digest: string;
  proposal_id: string | null;
  goal_id: string | null;
  task_intake_id: string | null;
  project_id: string | null;
  workspace_id: string | null;
  requester_ref: string;
  risk: string;
  approval_required: boolean;
  problem: string;
  scope: string[];
  out_of_scope: string[];
  acceptance_criteria: string[];
  dependencies: string[];
  constraints: string[];
  required_capabilities: string[];
  model_requirements: Record<string, unknown>;
  agent_requirements: Record<string, unknown>;
  data_security_constraints: string[];
  validation_strategy: string[];
  required_tests: string[];
  verification_requirements: string[];
  required_human_gates: string[];
  decomposition_hints: string[];
  assumptions: string[];
  open_questions: string[];
  provenance: Record<string, unknown>;
  specification_id?: string;
  updated_at: string;
}

export interface GovernanceAuditEvent {
  id: string;
  type: "governance-event";
  event_type: string;
  resource_type: string;
  resource_id: string;
  actor_ref: string;
  project_id: string | null;
  revision: number | null;
  digest: string | null;
  metadata: Record<string, unknown>;
  occurred_at: string;
}

export interface ApprovalRequestResult {
  id: string;
  type: "approval";
  status: string;
  specification_id: string;
  specification_revision: number;
  specification_digest: string;
  expires_at: string;
}

export interface TaskConversionResult {
  id: string;
  type: "task";
  status: string;
  project_id: string | null;
  governance: unknown;
}

const COMMAND_PATTERN = /^[a-z0-9][a-z0-9.-]*$/;

export class GovernanceClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: GovernanceClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  listProposals(query: ListQuery = {}): Promise<Page<GovernanceProposal>> {
    return this.collections.list<GovernanceProposal>("proposals", query);
  }

  getProposal(proposalId: string): Promise<GovernanceProposal> {
    return this.collections.get<GovernanceProposal>("proposals", proposalId);
  }

  listSpecifications(query: ListQuery = {}): Promise<Page<GovernanceSpecification>> {
    return this.collections.list<GovernanceSpecification>("specifications", query);
  }

  getSpecification(specificationId: string): Promise<GovernanceSpecification> {
    return this.collections.get<GovernanceSpecification>("specifications", specificationId);
  }

  listSpecificationRevisions(query: ListQuery = {}): Promise<Page<GovernanceSpecification>> {
    return this.collections.list<GovernanceSpecification>("specification-revisions", query);
  }

  listAuditEvents(query: ListQuery = {}): Promise<Page<GovernanceAuditEvent>> {
    return this.collections.list<GovernanceAuditEvent>("governance-events", query);
  }

  createProposal(payload: Record<string, unknown>): Promise<GovernanceProposal> {
    return this.command<GovernanceProposal>("proposal.create", "proposals", payload);
  }

  reviseProposal(
    proposalId: string,
    expectedRevision: number,
    payload: Record<string, unknown>,
  ): Promise<GovernanceProposal> {
    return this.command<GovernanceProposal>("proposal.revise", proposalId, {
      ...payload,
      expected_revision: expectedRevision,
    });
  }

  requestClarification(
    proposalId: string,
    expectedRevision: number,
  ): Promise<GovernanceProposal> {
    return this.command<GovernanceProposal>("proposal.request-clarification", proposalId, {
      expected_revision: expectedRevision,
    });
  }

  dismissProposal(proposalId: string, expectedRevision: number): Promise<GovernanceProposal> {
    return this.command<GovernanceProposal>("proposal.dismiss", proposalId, {
      expected_revision: expectedRevision,
    });
  }

  createSpecification(payload: Record<string, unknown>): Promise<GovernanceSpecification> {
    return this.command<GovernanceSpecification>("specification.create", "specifications", payload);
  }

  reviseSpecification(
    specificationId: string,
    expectedRevision: number,
    payload: Record<string, unknown>,
  ): Promise<GovernanceSpecification> {
    return this.command<GovernanceSpecification>("specification.revise", specificationId, {
      ...payload,
      expected_revision: expectedRevision,
    });
  }

  requestApproval(specificationId: string): Promise<ApprovalRequestResult> {
    return this.command<ApprovalRequestResult>(
      "specification.request-approval",
      specificationId,
      {},
    );
  }

  convertToTask(specificationId: string, approvalId?: string): Promise<TaskConversionResult> {
    return this.command<TaskConversionResult>(
      "specification.convert-to-task",
      specificationId,
      approvalId ? { approval_id: approvalId } : {},
    );
  }

  private async command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, unknown>,
  ): Promise<T> {
    if (!COMMAND_PATTERN.test(command)) throw new Error(`invalid canonical command: ${command}`);
    if (!resourceRef.trim()) throw new Error("resourceRef must not be blank");
    const correlationId = crypto.randomUUID();
    const response = await this.fetchImpl(
      `${this.baseUrl}/api/v1/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        headers: new Headers({
          Accept: "application/json",
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
          "X-Correlation-ID": correlationId,
        }),
        credentials: "include",
        body: JSON.stringify({ resource_ref: resourceRef, ...payload }),
      },
    );
    const text = await response.text();
    const decoded: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, decoded));
    }
    return decoded as T;
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
    typeof candidate.code === "string"
    && typeof candidate.category === "string"
    && typeof candidate.message === "string"
    && typeof candidate.request_id === "string"
    && typeof candidate.correlation_id === "string"
    && typeof candidate.retryable === "boolean"
  );
}
