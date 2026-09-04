import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export interface CanonicalAdapterMetadata {
  namespace: string;
  values: Record<string, JsonValue>;
}

export interface CanonicalSecretReference {
  provider: string;
  secret_id: string;
  scope: string;
  version?: string | null;
  metadata?: Record<string, JsonValue>;
}

export interface CanonicalConnectorDefinition {
  id: string;
  type: "connector-definition" | string;
  connector_type_id: string;
  name: string;
  version: string;
  description: string;
  supported_operations: string[];
  features: string[];
  authentication_requirements: string[];
  resource_types: string[];
  actions: string[];
  event_types: string[];
  configuration_schema: Record<string, JsonValue>;
  health_semantics: Record<string, JsonValue>;
  source_metadata: CanonicalAdapterMetadata[];
}

export interface CanonicalConnection {
  id: string;
  type: "connection" | string;
  connector_type_id: string;
  connector_version: string;
  owner_type: string;
  owner_id: string;
  display_name: string;
  project_id: string | null;
  organization_id: string | null;
  endpoint_metadata: Record<string, JsonValue>;
  account_metadata: CanonicalAdapterMetadata[];
  secret_references: CanonicalSecretReference[];
  requested_scopes: string[];
  granted_scopes: string[];
  enabled: boolean;
  status: string;
  health: string;
  created_at: string;
  updated_at: string;
  last_checked_at: string | null;
  revision: number;
}

export interface CreateConnectionInput {
  connector_type_id: string;
  connector_version: string;
  owner_type: string;
  owner_id: string;
  display_name: string;
  project_id?: string;
  organization_id?: string;
  endpoint_metadata?: Record<string, JsonValue>;
  secret_references?: CanonicalSecretReference[];
  requested_scopes?: string[];
  approval_id?: string;
}

export type ConnectorSyncMode = "incremental" | "resync" | "rebuild";

export interface ConnectorSyncResult {
  connection_id: string;
  stream: string;
  mode: ConnectorSyncMode | string;
  cursor: string | null;
  status: string;
  last_successful_sync: string | null;
  resource_refs: JsonValue[];
  events: JsonValue[];
}

export interface IntegrationsClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const DEFINITIONS = "connector-definitions";
const CONNECTIONS = "connections";

export class IntegrationsClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: IntegrationsClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  listDefinitions(query: ListQuery = {}): Promise<Page<CanonicalConnectorDefinition>> {
    return this.collections.list<CanonicalConnectorDefinition>(DEFINITIONS, query);
  }

  getDefinition(definitionId: string): Promise<CanonicalConnectorDefinition> {
    return this.collections.get<CanonicalConnectorDefinition>(
      DEFINITIONS,
      requireRef(definitionId, "connector definition"),
    );
  }

  listConnections(query: ListQuery = {}): Promise<Page<CanonicalConnection>> {
    return this.collections.list<CanonicalConnection>(CONNECTIONS, query);
  }

  getConnection(connectionId: string): Promise<CanonicalConnection> {
    return this.collections.get<CanonicalConnection>(
      CONNECTIONS,
      requireRef(connectionId, "connection"),
    );
  }

  createConnection(
    input: CreateConnectionInput,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalConnection> {
    validateCreateInput(input);
    return this.command<CanonicalConnection>(
      "connection.create",
      CONNECTIONS,
      compactCreateInput(input),
      idempotencyKey,
    );
  }

  enableConnection(
    connectionId: string,
    approvalId?: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalConnection> {
    return this.command<CanonicalConnection>(
      "connection.enable",
      requireRef(connectionId, "connection"),
      optionalApprovalPayload(approvalId),
      idempotencyKey,
    );
  }

  disableConnection(
    connectionId: string,
    approvalId?: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalConnection> {
    return this.command<CanonicalConnection>(
      "connection.disable",
      requireRef(connectionId, "connection"),
      optionalApprovalPayload(approvalId),
      idempotencyKey,
    );
  }

  removeConnection(
    connectionId: string,
    approvalId?: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<{ id: string; removed: boolean }> {
    return this.command<{ id: string; removed: boolean }>(
      "connection.remove",
      requireRef(connectionId, "connection"),
      optionalApprovalPayload(approvalId),
      idempotencyKey,
    );
  }

  checkConnectionHealth(
    connectionId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalConnection> {
    return this.command<CanonicalConnection>(
      "connection.health",
      requireRef(connectionId, "connection"),
      {},
      idempotencyKey,
    );
  }

  synchronize(
    connectionId: string,
    stream: string,
    mode: ConnectorSyncMode = "incremental",
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<ConnectorSyncResult> {
    if (!stream.trim()) throw new Error("connector sync stream is required");
    if (!(["incremental", "resync", "rebuild"] as const).includes(mode)) {
      throw new Error("connector sync mode is invalid");
    }
    return this.command<ConnectorSyncResult>(
      "connector.sync",
      requireRef(connectionId, "connection"),
      { stream: stream.trim(), mode },
      idempotencyKey,
    );
  }

  private async command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, JsonValue>,
    idempotencyKey: string,
  ): Promise<T> {
    if (!idempotencyKey.trim()) throw new Error("integration idempotency key is required");
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
    const responsePayload: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, responsePayload));
    }
    return responsePayload as T;
  }
}

function validateCreateInput(input: CreateConnectionInput): void {
  for (const [label, value] of [
    ["connector type", input.connector_type_id],
    ["connector version", input.connector_version],
    ["owner type", input.owner_type],
    ["owner id", input.owner_id],
    ["display name", input.display_name],
  ] as const) {
    if (!value.trim()) throw new Error(`${label} is required`);
  }
  if (input.project_id !== undefined && !input.project_id.trim()) {
    throw new Error("project id must be omitted or non-blank");
  }
  if (input.organization_id !== undefined && !input.organization_id.trim()) {
    throw new Error("organization id must be omitted or non-blank");
  }
  if (input.requested_scopes?.some((scope) => !scope.trim())) {
    throw new Error("requested scopes must not contain blank values");
  }
  for (const reference of input.secret_references ?? []) {
    if (!reference.provider.trim() || !reference.secret_id.trim() || !reference.scope.trim()) {
      throw new Error("secret references require provider, secret_id and scope");
    }
  }
}

function compactCreateInput(input: CreateConnectionInput): Record<string, JsonValue> {
  const payload: Record<string, JsonValue> = {
    connector_type_id: input.connector_type_id.trim(),
    connector_version: input.connector_version.trim(),
    owner_type: input.owner_type.trim(),
    owner_id: input.owner_id.trim(),
    display_name: input.display_name.trim(),
    endpoint_metadata: input.endpoint_metadata ?? {},
    secret_references: (input.secret_references ?? []).map((reference) => ({
      provider: reference.provider,
      secret_id: reference.secret_id,
      scope: reference.scope,
      version: reference.version ?? null,
      metadata: reference.metadata ?? {},
    })),
    requested_scopes: (input.requested_scopes ?? []).map((scope) => scope.trim()),
  };
  if (input.project_id) payload.project_id = input.project_id.trim();
  if (input.organization_id) payload.organization_id = input.organization_id.trim();
  if (input.approval_id) payload.approval_id = input.approval_id.trim();
  return payload;
}

function optionalApprovalPayload(approvalId?: string): Record<string, JsonValue> {
  return approvalId?.trim() ? { approval_id: approvalId.trim() } : {};
}

function requireRef(value: string, label: string): string {
  if (!value.trim()) throw new Error(`${label} reference is required`);
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
