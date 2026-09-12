import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
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

export interface RepositoryStatusView {
  repository_id: string;
  head_revision: string | null;
  branch: string | null;
  clean: boolean;
  staged_paths: string[];
  modified_paths: string[];
  deleted_paths: string[];
  untracked_paths: string[];
}

export interface RepositoryCommitView {
  repository_id: string;
  revision: string;
  message: string;
  parent_revisions: string[];
}

export interface RepositoryDiffView {
  repository_id: string;
  base_revision: string | null;
  patch: string;
  changed_paths: string[];
}

export interface RepositoryClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

/** Provider-neutral frontend client for canonical repository resources and commands. */
export class RepositoryCollectionClient {
  readonly baseUrl: string;
  private readonly collections: ControlPlaneCollectionClient;
  private readonly transport: ApiTransport;

  constructor(options: RepositoryClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
    this.collections = new ControlPlaneCollectionClient({ transport: this.transport });
  }

  list(query: ListQuery = {}): Promise<Page<CanonicalRepository>> {
    return this.collections.list<CanonicalRepository>("repositories", query);
  }

  get(repositoryId: string): Promise<CanonicalRepository> {
    return this.collections.get<CanonicalRepository>(
      "repositories",
      requireRef(repositoryId, "repository"),
    );
  }

  status(repositoryId: string, approvalId?: string): Promise<RepositoryStatusView> {
    return this.command<RepositoryStatusView>(
      "repository.status",
      repositoryId,
      approvalPayload(approvalId),
    );
  }

  branches(repositoryId: string, approvalId?: string): Promise<string[]> {
    return this.command<{ repository_id: string; branches: string[] }>(
      "repository.branches",
      repositoryId,
      approvalPayload(approvalId),
    ).then((value) => value.branches);
  }

  tags(repositoryId: string, approvalId?: string): Promise<string[]> {
    return this.command<{ repository_id: string; tags: string[] }>(
      "repository.tags",
      repositoryId,
      approvalPayload(approvalId),
    ).then((value) => value.tags);
  }

  commits(repositoryId: string, revision: string = "HEAD", limit: number = 20): Promise<RepositoryCommitView[]> {
    if (!revision.trim()) throw new Error("repository revision is required");
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
      throw new Error("repository commit limit must be between 1 and 200");
    }
    return this.command<{ repository_id: string; commits: RepositoryCommitView[] }>(
      "repository.commits",
      repositoryId,
      { revision: revision.trim(), limit },
    ).then((value) => value.commits);
  }

  diff(repositoryId: string, baseRevision?: string): Promise<RepositoryDiffView> {
    return this.command<RepositoryDiffView>(
      "repository.diff",
      repositoryId,
      baseRevision?.trim() ? { base_revision: baseRevision.trim() } : {},
    );
  }

  fetch(
    repositoryId: string,
    approvalId?: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<Record<string, JsonValue>> {
    return this.command<Record<string, JsonValue>>(
      "repository.fetch",
      repositoryId,
      approvalPayload(approvalId),
      idempotencyKey,
    );
  }

  detach(
    repositoryId: string,
    approvalId?: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<{ repository_id: string; detached: boolean }> {
    return this.command<{ repository_id: string; detached: boolean }>(
      "repository.detach",
      repositoryId,
      approvalPayload(approvalId),
      idempotencyKey,
    );
  }

  private command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, JsonValue>,
    idempotencyKey?: string,
  ): Promise<T> {
    if (idempotencyKey !== undefined && !idempotencyKey.trim()) {
      throw new Error("repository idempotency key is required");
    }
    return this.transport.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      body: { resource_ref: requireRef(resourceRef, "repository"), ...payload },
      idempotencyKey,
    });
  }
}

function requireRef(value: string, label: string): string {
  if (!value.trim()) throw new Error(`${label} reference is required`);
  return value.trim();
}

function approvalPayload(approvalId?: string): Record<string, JsonValue> {
  return approvalId?.trim() ? { approval_id: approvalId.trim() } : {};
}
