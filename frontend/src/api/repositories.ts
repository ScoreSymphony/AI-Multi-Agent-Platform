import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

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

export interface RepositoryClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

/** Provider-neutral frontend client for canonical repository resources and commands. */
export class RepositoryCollectionClient {
  readonly baseUrl: string;
  private readonly collections: ControlPlaneCollectionClient;
  private readonly fetchImpl: typeof fetch;

  constructor(options: RepositoryClientOptions | ControlPlaneCollectionClient = {}) {
    if (options instanceof ControlPlaneCollectionClient) {
      this.baseUrl = "";
      this.collections = options;
      this.fetchImpl = fetch;
      return;
    }
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
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

  private async command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, JsonValue>,
    idempotencyKey?: string,
  ): Promise<T> {
    const headers = new Headers({
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
    });
    if (idempotencyKey !== undefined) {
      if (!idempotencyKey.trim()) throw new Error("repository idempotency key is required");
      headers.set("Idempotency-Key", idempotencyKey);
    }
    const response = await this.fetchImpl(
      `${this.baseUrl}/api/v1/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify({ resource_ref: requireRef(resourceRef, "repository"), ...payload }),
      },
    );
    const text = await response.text();
    const responsePayload: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, responsePayload));
    }
    return responsePayload as T;
  }
}

function requireRef(value: string, label: string): string {
  if (!value.trim()) throw new Error(`${label} reference is required`);
  return value.trim();
}

function approvalPayload(approvalId?: string): Record<string, JsonValue> {
  return approvalId?.trim() ? { approval_id: approvalId.trim() } : {};
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
