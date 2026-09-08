import { ControlPlaneCollectionClient, type ControlPlaneCollectionClientOptions } from "./collections";
import type { JsonValue } from "./types";

export interface CanonicalContextRunBinding {
  id: string;
  agent_run_id: string;
  run_id: string;
  task_id: string;
  agent_id: string;
  agent_revision: number;
  context_bundle_id: string;
  context_bundle_digest: string;
  resolver_version: string;
  policy_version: string;
  orchestrator_adapter_id: string;
  created_at: string;
}

export interface CanonicalContextEntry {
  ordinal: number;
  source_type: string;
  mandatory: boolean;
  role: string;
  selection_reason: string;
  freshness: string;
  data_classification: string;
  estimated_tokens: number;
  content_bytes: number;
  hidden: boolean;
  source_id?: string;
  source_revision?: string | number | null;
  source_digest?: string | null;
  source_snapshot_id?: string | null;
  source_locator?: string | null;
  content_digest?: string;
  content_ref?: string | null;
  inline_content?: null;
  trust?: string;
  priority?: number;
  relevance?: number;
  project_id?: string | null;
  workspace_id?: string | null;
  security_labels?: string[];
  transformation?: JsonValue;
}

export interface CanonicalContextOmission {
  source_type: string;
  reason: string;
  mandatory: boolean;
}

export interface CanonicalContextBundle {
  id: string;
  context_bundle_id: string;
  digest: string;
  task_id: string;
  run_id: string;
  agent_id: string;
  agent_revision: number;
  plan_id: string | null;
  step_id: string | null;
  skill_bundle_id: string | null;
  skill_bundle_digest: string | null;
  entries: CanonicalContextEntry[];
  omissions: CanonicalContextOmission[];
  budget: Record<string, JsonValue>;
  usage: Record<string, JsonValue>;
  resolver_version: string;
  policy_version: string;
  created_at: string;
  reproducibility_limited: boolean;
}

export interface CanonicalRunContextInspection {
  bindings: CanonicalContextRunBinding[];
  bundles: CanonicalContextBundle[];
}

export class ContextInspectionClient {
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: ControlPlaneCollectionClientOptions = {}) {
    this.collections = new ControlPlaneCollectionClient(options);
  }

  async forRun(runId: string): Promise<CanonicalRunContextInspection> {
    const page = await this.collections.list<CanonicalContextRunBinding>(
      "context-run-bindings",
      {
        limit: 100,
        sort: "id",
        direction: "asc",
        filters: { run_id: runId },
      },
    );
    const bundles = await Promise.all(
      page.items.map((binding) => this.getBundle(binding.context_bundle_id)),
    );
    return { bindings: page.items, bundles };
  }

  getBundle(contextBundleId: string): Promise<CanonicalContextBundle> {
    return this.collections.get<CanonicalContextBundle>("context-bundles", contextBundleId);
  }

  getBinding(agentRunId: string): Promise<CanonicalContextRunBinding> {
    return this.collections.get<CanonicalContextRunBinding>("context-run-bindings", agentRunId);
  }
}
