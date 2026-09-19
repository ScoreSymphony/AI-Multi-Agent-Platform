import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

export type DecisionOutcome =
  | "adopt"
  | "reject"
  | "defer"
  | "experimental"
  | "supersede"
  | "custom";
export type DecisionStatus = "current" | "superseded" | "withdrawn";
export type DecisionAlternativeStatus = "considered" | "rejected" | "selected";

export interface DecisionReference {
  kind: string;
  resource_id: string;
  revision: string | number | null;
  digest: string | null;
  locator: string | null;
  metadata: Record<string, JsonValue>;
}

export interface DecisionAlternative {
  label: string;
  status: DecisionAlternativeStatus;
  resource_ref: DecisionReference | null;
  evidence_refs: DecisionReference[];
  trade_offs: string[];
  unknowns: string[];
}

export interface CanonicalDecisionRecord {
  id: string;
  type: "decision-record";
  title: string;
  subject: string;
  category: string;
  scope_type: string;
  scope_id: string | null;
  subject_ref: DecisionReference | null;
  question: string;
  alternatives: DecisionAlternative[];
  outcome: DecisionOutcome;
  rationale: string;
  evidence_refs: DecisionReference[];
  evaluation_refs: DecisionReference[];
  finding_refs: DecisionReference[];
  cost_resource_refs: DecisionReference[];
  actor_ref: string;
  reviewer_refs: string[];
  approval_ref: DecisionReference | null;
  adr_ref: DecisionReference | null;
  effective_at: string;
  review_at: string | null;
  review_condition: string | null;
  status: DecisionStatus;
  supersedes: string | null;
  superseded_by: string | null;
  withdrawn_at: string | null;
  withdrawal_reason: string | null;
  downstream_refs: DecisionReference[];
  revision: number;
  content_digest: string;
  created_at: string;
  schema_version: string;
  revisit_due: boolean;
}

export interface DecisionRecordClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export class DecisionRecordClient {
  private readonly transport: ApiTransport;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: DecisionRecordClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.collections = new ControlPlaneCollectionClient({ transport: this.transport });
  }

  list(query: ListQuery = {}): Promise<Page<CanonicalDecisionRecord>> {
    return this.collections.list<CanonicalDecisionRecord>("decision-records", query);
  }

  get(decisionRecordId: string): Promise<CanonicalDecisionRecord> {
    return this.collections.get<CanonicalDecisionRecord>("decision-records", decisionRecordId);
  }

  create(payload: Record<string, JsonValue>): Promise<CanonicalDecisionRecord> {
    return this.command("decision-record.create", "decision-records", payload);
  }

  supersede(
    decisionRecordId: string,
    payload: Record<string, JsonValue>,
  ): Promise<CanonicalDecisionRecord> {
    return this.command("decision-record.supersede", decisionRecordId, payload);
  }

  withdraw(decisionRecordId: string, reason: string): Promise<CanonicalDecisionRecord> {
    return this.command("decision-record.withdraw", decisionRecordId, { reason });
  }

  linkProvenance(
    decisionRecordId: string,
    reference: DecisionReference,
  ): Promise<CanonicalDecisionRecord> {
    return this.command("decision-record.link-provenance", decisionRecordId, {
      reference: {
        kind: reference.kind,
        resource_id: reference.resource_id,
        revision: reference.revision,
        digest: reference.digest,
        locator: reference.locator,
        metadata: reference.metadata,
      },
    });
  }

  private command(
    command: string,
    resourceRef: string,
    payload: Record<string, JsonValue>,
  ): Promise<CanonicalDecisionRecord> {
    return this.transport.request<CanonicalDecisionRecord>(
      `/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        idempotencyKey: crypto.randomUUID(),
        body: { resource_ref: resourceRef, ...payload },
      },
    );
  }
}
