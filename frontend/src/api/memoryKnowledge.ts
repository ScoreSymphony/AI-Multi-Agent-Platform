import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export type MemoryScope =
  | "short_term"
  | "task"
  | "agent"
  | "workspace"
  | "user"
  | "historical"
  | "organization";
export type MemoryOrigin = "user-authored" | "agent-derived" | "imported";
export type MemoryRetention =
  | "ephemeral"
  | "task_lifetime"
  | "project_lifetime"
  | "user_lifetime"
  | "durable"
  | "until";
export type KnowledgeStatus = "registered" | "indexing" | "ready" | "failed" | "removed";
export type KnowledgeSearchMode = "keyword" | "semantic" | "hybrid";

export interface CanonicalSourceRef {
  kind: string;
  ref: string;
  location: string | null;
  revision: string | null;
  checksum: string | null;
}

export interface CanonicalMemoryEntry {
  id: string;
  type: "memory";
  scope: MemoryScope;
  scope_id: string;
  project_id: string | null;
  owner_ref: string;
  created_by: string;
  created_at: string;
  value: JsonValue;
  origin: MemoryOrigin;
  retention: MemoryRetention;
  expires_at: string | null;
  provenance: CanonicalSourceRef[];
  supersedes_memory_id: string | null;
  superseded_by_memory_id: string | null;
  classification: string | null;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalKnowledgeSource {
  id: string;
  type: "knowledge-source";
  project_id: string | null;
  owner_ref: string;
  created_by: string;
  title: string;
  revision: string;
  status: KnowledgeStatus;
  created_at: string;
  updated_at: string;
  content_checksum: string | null;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalKnowledgeResult {
  id: string;
  type: "knowledge-result";
  source_id: string;
  project_id: null;
  owner_ref: null;
  revision: string;
  content: string;
  location: string;
  score: number | null;
  citation: CanonicalSourceRef;
}

export interface MemoryListInput {
  scope?: MemoryScope;
  scopeId?: string;
  projectId?: string;
  ownerRef?: string;
  includeExpired?: boolean;
  includeSuperseded?: boolean;
  search?: string;
  limit?: number;
  cursor?: string;
}

export interface CreateMemoryInput {
  scope: MemoryScope;
  scopeId: string;
  origin: MemoryOrigin;
  value: JsonValue;
  retention?: MemoryRetention;
  expiresAt?: string | null;
  projectId?: string | null;
  classification?: string | null;
  metadata?: Record<string, JsonValue>;
  provenance?: CanonicalSourceRef[];
}

export interface PromoteMemoryInput {
  scope: Exclude<MemoryScope, "short_term">;
  scopeId: string;
  retention?: MemoryRetention;
  expiresAt?: string | null;
  projectId?: string | null;
}

export interface UpdateMemoryInput {
  value?: JsonValue;
  retention?: MemoryRetention;
  expiresAt?: string | null;
  classification?: string | null;
  metadata?: Record<string, JsonValue>;
}

export interface ExpireMemoryInput {
  scope: MemoryScope;
  scopeId: string;
  projectId?: string | null;
}

export interface KnowledgeListInput {
  projectId?: string;
  limit?: number;
  cursor?: string;
}

export interface KnowledgeSearchInput {
  query: string;
  mode?: KnowledgeSearchMode;
  sourceId?: string;
  projectId?: string;
  limit?: number;
  cursor?: string;
}

export interface RegisterKnowledgeInput {
  targetRef: string;
  title: string;
  revision?: string;
  projectId?: string | null;
  metadata?: Record<string, JsonValue>;
}

export interface UpdateKnowledgeInput {
  title?: string;
  metadata?: Record<string, JsonValue>;
}

export interface IngestKnowledgeInput {
  content: string;
  location: string;
}

export interface ReindexKnowledgeInput extends IngestKnowledgeInput {
  revision: string;
}

export interface MemoryKnowledgeClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const MEMORY = "memory";
const KNOWLEDGE = "knowledge";
const KNOWLEDGE_RESULTS = "knowledge-results";

export class MemoryKnowledgeClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: MemoryKnowledgeClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  listMemory(input: MemoryListInput = {}): Promise<Page<CanonicalMemoryEntry>> {
    const filters: Record<string, string> = {};
    setOptionalFilter(filters, "scope", input.scope);
    setOptionalFilter(filters, "scope_id", input.scopeId);
    setOptionalFilter(filters, "project_id", input.projectId);
    setOptionalFilter(filters, "owner_ref", input.ownerRef);
    if (input.includeExpired) filters.include_expired = "true";
    if (input.includeSuperseded) filters.include_superseded = "true";
    return this.collections.list<CanonicalMemoryEntry>(MEMORY, {
      limit: input.limit,
      cursor: input.cursor,
      q: optionalNonBlank(input.search) ?? undefined,
      filters,
    });
  }

  getMemory(memoryId: string): Promise<CanonicalMemoryEntry> {
    return this.collections.get<CanonicalMemoryEntry>(MEMORY, requireNonBlank(memoryId, "memory id"));
  }

  createMemory(
    input: CreateMemoryInput,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalMemoryEntry> {
    const scopeId = requireNonBlank(input.scopeId, "memory scope id");
    return this.command<CanonicalMemoryEntry>(
      "memory.create",
      scopeId,
      compactPayload({
        scope: input.scope,
        scope_id: scopeId,
        origin: input.origin,
        value: input.value,
        retention: input.retention,
        expires_at: optionalNonBlank(input.expiresAt) ?? undefined,
        project_id: optionalNonBlank(input.projectId) ?? undefined,
        classification: optionalNonBlank(input.classification) ?? undefined,
        metadata: input.metadata,
        provenance: input.provenance?.map(sourceRefPayload),
      }),
      idempotencyKey,
    );
  }

  promoteMemory(
    memoryId: string,
    input: PromoteMemoryInput,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalMemoryEntry> {
    return this.command<CanonicalMemoryEntry>(
      "memory.promote",
      requireNonBlank(memoryId, "memory id"),
      compactPayload({
        scope: input.scope,
        scope_id: requireNonBlank(input.scopeId, "target memory scope id"),
        retention: input.retention,
        expires_at: optionalNonBlank(input.expiresAt) ?? undefined,
        project_id: optionalNonBlank(input.projectId) ?? undefined,
      }),
      idempotencyKey,
    );
  }

  updateMemory(
    memoryId: string,
    input: UpdateMemoryInput,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalMemoryEntry> {
    if (
      input.value === undefined
      && input.retention === undefined
      && input.expiresAt === undefined
      && input.classification === undefined
      && input.metadata === undefined
    ) {
      throw new Error("memory update requires at least one mutable field");
    }
    return this.command<CanonicalMemoryEntry>(
      "memory.update",
      requireNonBlank(memoryId, "memory id"),
      compactPayload({
        value: input.value,
        retention: input.retention,
        expires_at: input.expiresAt === null ? null : optionalNonBlank(input.expiresAt) ?? undefined,
        classification: input.classification === null
          ? null
          : optionalNonBlank(input.classification) ?? undefined,
        metadata: input.metadata,
      }),
      idempotencyKey,
    );
  }

  expireMemory(
    memoryId: string,
    input: ExpireMemoryInput,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<{ id: string; type: "memory"; expired: true }> {
    return this.command(
      "memory.expire",
      requireNonBlank(memoryId, "memory id"),
      compactPayload({
        scope: input.scope,
        scope_id: requireNonBlank(input.scopeId, "memory scope id"),
        project_id: optionalNonBlank(input.projectId) ?? undefined,
      }),
      idempotencyKey,
    );
  }

  deleteMemory(
    memoryId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<{ id: string; type: "memory"; deleted: true }> {
    return this.command(
      "memory.delete",
      requireNonBlank(memoryId, "memory id"),
      {},
      idempotencyKey,
    );
  }

  listKnowledge(input: KnowledgeListInput = {}): Promise<Page<CanonicalKnowledgeSource>> {
    const filters: Record<string, string> = {};
    setOptionalFilter(filters, "project_id", input.projectId);
    return this.collections.list<CanonicalKnowledgeSource>(KNOWLEDGE, {
      limit: input.limit,
      cursor: input.cursor,
      filters,
    });
  }

  getKnowledge(sourceId: string): Promise<CanonicalKnowledgeSource> {
    return this.collections.get<CanonicalKnowledgeSource>(
      KNOWLEDGE,
      requireNonBlank(sourceId, "knowledge source id"),
    );
  }

  searchKnowledge(input: KnowledgeSearchInput): Promise<Page<CanonicalKnowledgeResult>> {
    const query = requireNonBlank(input.query, "knowledge query");
    const filters: Record<string, string> = { mode: input.mode ?? "keyword" };
    setOptionalFilter(filters, "source_id", input.sourceId);
    setOptionalFilter(filters, "project_id", input.projectId);
    return this.collections.list<CanonicalKnowledgeResult>(KNOWLEDGE_RESULTS, {
      limit: input.limit,
      cursor: input.cursor,
      q: query,
      filters,
    });
  }

  registerKnowledge(
    input: RegisterKnowledgeInput,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalKnowledgeSource> {
    return this.command<CanonicalKnowledgeSource>(
      "knowledge.register",
      requireNonBlank(input.targetRef, "knowledge target ref"),
      compactPayload({
        title: requireNonBlank(input.title, "knowledge title"),
        revision: optionalNonBlank(input.revision) ?? undefined,
        project_id: optionalNonBlank(input.projectId) ?? undefined,
        metadata: input.metadata,
      }),
      idempotencyKey,
    );
  }

  updateKnowledge(
    sourceId: string,
    input: UpdateKnowledgeInput,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalKnowledgeSource> {
    const title = optionalNonBlank(input.title);
    if (title === null && input.metadata === undefined) {
      throw new Error("knowledge update requires title and/or metadata");
    }
    return this.command<CanonicalKnowledgeSource>(
      "knowledge.update",
      requireNonBlank(sourceId, "knowledge source id"),
      compactPayload({ title: title ?? undefined, metadata: input.metadata }),
      idempotencyKey,
    );
  }

  ingestKnowledge(
    sourceId: string,
    input: IngestKnowledgeInput,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<JsonValue> {
    return this.command(
      "knowledge.ingest",
      requireNonBlank(sourceId, "knowledge source id"),
      {
        content: requireNonBlank(input.content, "knowledge content"),
        location: requireNonBlank(input.location, "knowledge location"),
      },
      idempotencyKey,
    );
  }

  reindexKnowledge(
    sourceId: string,
    input: ReindexKnowledgeInput,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<JsonValue> {
    return this.command(
      "knowledge.reindex",
      requireNonBlank(sourceId, "knowledge source id"),
      {
        revision: requireNonBlank(input.revision, "knowledge revision"),
        content: requireNonBlank(input.content, "knowledge content"),
        location: requireNonBlank(input.location, "knowledge location"),
      },
      idempotencyKey,
    );
  }

  detachKnowledge(
    sourceId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalKnowledgeSource & { detached: true }> {
    return this.command(
      "knowledge.detach",
      requireNonBlank(sourceId, "knowledge source id"),
      {},
      idempotencyKey,
    );
  }

  deleteKnowledge(
    sourceId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalKnowledgeSource & { deleted: true }> {
    return this.command(
      "knowledge.delete",
      requireNonBlank(sourceId, "knowledge source id"),
      {},
      idempotencyKey,
    );
  }

  private async command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, JsonValue>,
    idempotencyKey: string,
  ): Promise<T> {
    if (!idempotencyKey.trim()) throw new Error("idempotency key is required");
    const headers = new Headers({
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
      "Idempotency-Key": idempotencyKey,
    });
    const response = await this.fetchImpl(
      `${this.baseUrl}/api/v1/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify({ resource_ref: resourceRef, ...payload }),
      },
    );
    const text = await response.text();
    const body: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, body));
    }
    return body as T;
  }
}

function sourceRefPayload(source: CanonicalSourceRef): JsonValue {
  return {
    kind: source.kind,
    ref: source.ref,
    location: source.location,
    revision: source.revision,
    checksum: source.checksum,
  };
}

function compactPayload(values: Record<string, JsonValue | undefined>): Record<string, JsonValue> {
  const payload: Record<string, JsonValue> = {};
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) payload[key] = value;
  }
  return payload;
}

function setOptionalFilter(filters: Record<string, string>, key: string, value: string | null | undefined): void {
  const normalized = optionalNonBlank(value);
  if (normalized !== null) filters[key] = normalized;
}

function requireNonBlank(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} is required`);
  return normalized;
}

function optionalNonBlank(value: string | null | undefined): string | null {
  if (value === undefined || value === null) return null;
  const normalized = value.trim();
  return normalized ? normalized : null;
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
