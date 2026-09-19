import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

export type ResearchClass = "task_research" | "project_research" | "domain_research";

export interface ResearchVerificationBinding {
  binding_id: string;
  verification_id: string;
  subject_type: string;
  subject_id: string;
  subject_revision: number;
  subject_digest: string;
  created_at: string;
}

export interface CanonicalResearchItem {
  id: string;
  type: "research-item";
  title: string;
  question: string;
  research_class: ResearchClass | string;
  status: string;
  revision: number;
  digest: string;
  owner_type: string;
  owner_id: string;
  project_id: string | null;
  workspace_id: string | null;
  task_id: string | null;
  plan_id: string | null;
  run_id: string | null;
  data_class: string;
  constraints: string[];
  source_ids: string[];
  claim_ids: string[];
  evidence_ids: string[];
  verification_ids: string[];
  verification_bindings: ResearchVerificationBinding[];
  evidence_freshness: Record<string, string>;
  freshness_policy: {
    max_age_seconds: number | null;
    revalidate_on_source_change: boolean;
  };
  supersedes_research_item_id: string | null;
  created_at: string;
  updated_at: string;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalResearchSource {
  id: string;
  type: "research-source";
  research_item_id: string;
  source_type: string;
  locator: string;
  title: string;
  author: string | null;
  publisher: string | null;
  license_ref: string | null;
  trust_classification: string;
  current_observation_id: string | null;
  observation_ids: string[];
  current_observation_state: string | null;
  current_binding: Record<string, JsonValue> | null;
  created_at: string;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalResearchObservation {
  id: string;
  type: "research-source-observation";
  research_item_id: string;
  source_id: string;
  retrieved_at: string;
  state: string;
  identity_proven: boolean;
  revision: string | null;
  version: string | null;
  commit: string | null;
  etag: string | null;
  content_digest: string | null;
  snapshot_digest: string | null;
  snapshot_artifact_id: string | null;
  repository_id: string | null;
  requested_repository_revision: string | null;
  resolved_repository_revision: string | null;
  intelligence_provider_id: string | null;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalResearchClaim {
  id: string;
  type: "research-claim";
  research_item_id: string;
  revision: number;
  digest: string;
  text: string;
  category: string;
  confidence: string;
  status: string;
  evidence_ids: string[];
  author_ref: string | null;
  agent_id: string | null;
  agent_revision: number | null;
  run_id: string | null;
  supersedes_claim_id: string | null;
  created_at: string;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalResearchEvidence {
  id: string;
  type: "research-evidence";
  research_item_id: string;
  source_id: string;
  source_observation_id: string;
  claim_id: string;
  relation: string;
  freshness: string;
  digest: string;
  retrieved_at: string;
  task_id: string | null;
  run_id: string | null;
  agent_id: string | null;
  agent_revision: number | null;
  location_ref: string | null;
  source_revision: string | null;
  source_version: string | null;
  source_commit: string | null;
  source_etag: string | null;
  source_content_digest: string | null;
  source_snapshot_digest: string | null;
  artifact_id: string | null;
  excerpt_digest: string | null;
  extraction_method: string;
  supersedes_evidence_id: string | null;
  created_at: string;
  metadata: Record<string, JsonValue>;
}

export interface ResearchClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export const RESEARCH_COLLECTIONS = [
  "research-items",
  "research-sources",
  "research-source-observations",
  "research-claims",
  "research-evidence",
] as const;

export class ResearchClient {
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: ResearchClientOptions = {}) {
    const transport = options.transport ?? new ApiTransport(options);
    this.collections = new ControlPlaneCollectionClient({ transport });
  }

  listItems(query: ListQuery = {}): Promise<Page<CanonicalResearchItem>> {
    return this.collections.list("research-items", query);
  }

  getItem(id: string): Promise<CanonicalResearchItem> {
    return this.collections.get("research-items", id);
  }

  listSources(query: ListQuery = {}): Promise<Page<CanonicalResearchSource>> {
    return this.collections.list("research-sources", query);
  }

  getSource(id: string): Promise<CanonicalResearchSource> {
    return this.collections.get("research-sources", id);
  }

  listObservations(query: ListQuery = {}): Promise<Page<CanonicalResearchObservation>> {
    return this.collections.list("research-source-observations", query);
  }

  getObservation(id: string): Promise<CanonicalResearchObservation> {
    return this.collections.get("research-source-observations", id);
  }

  listClaims(query: ListQuery = {}): Promise<Page<CanonicalResearchClaim>> {
    return this.collections.list("research-claims", query);
  }

  getClaim(id: string): Promise<CanonicalResearchClaim> {
    return this.collections.get("research-claims", id);
  }

  listEvidence(query: ListQuery = {}): Promise<Page<CanonicalResearchEvidence>> {
    return this.collections.list("research-evidence", query);
  }

  getEvidence(id: string): Promise<CanonicalResearchEvidence> {
    return this.collections.get("research-evidence", id);
  }
}
