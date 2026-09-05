import { ControlPlaneCollectionClient } from "./collections";
import type { JsonValue, ListQuery, Page } from "./types";

export interface CanonicalRepositoryCapability {
  operation: string;
  side_effects: string;
  requires_credentials: boolean;
  supported: boolean;
}

export interface CanonicalRepository {
  id: string;
  connection_id: string;
  external_resource: Record<string, JsonValue>;
  default_branch: string | null;
  target_revision: string | null;
  resolved_revision: string | null;
  visibility: string;
  capabilities: CanonicalRepositoryCapability[];
  metadata: Record<string, JsonValue>;
}

/**
 * Provider-neutral frontend read hook for repositories registered through the
 * canonical Control Plane extension collection.
 */
export class RepositoryCollectionClient {
  private readonly collections: ControlPlaneCollectionClient;

  constructor(collections: ControlPlaneCollectionClient = new ControlPlaneCollectionClient()) {
    this.collections = collections;
  }

  list(query: ListQuery = {}): Promise<Page<CanonicalRepository>> {
    return this.collections.list<CanonicalRepository>("repositories", query);
  }

  get(repositoryId: string): Promise<CanonicalRepository> {
    return this.collections.get<CanonicalRepository>("repositories", repositoryId);
  }
}
