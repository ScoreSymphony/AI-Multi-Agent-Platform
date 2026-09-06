import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export type RegistryItemType =
  | "agent"
  | "agent_team"
  | "tool"
  | "plugin"
  | "workflow"
  | "template"
  | "model_configuration"
  | "connector"
  | "evaluation"
  | "documentation";

export type RegistryTrustStatus = "untrusted" | "reviewed" | "trusted" | "local";
export type RegistryRoute = "plugin" | "portable_import" | "manual";

export interface RegistryDependency {
  item_id: string;
  minimum_version: string | null;
  maximum_version: string | null;
  optional: boolean;
}

export interface RegistryItem {
  id: string;
  type: "registry-item";
  item_id: string;
  item_type: RegistryItemType;
  name: string;
  description: string;
  version: string;
  publisher: string;
  source: {
    repository: string;
    package_reference: string | null;
    revision: string | null;
  };
  license: string;
  provenance: string;
  minimum_platform_version: string | null;
  maximum_platform_version: string | null;
  dependencies: RegistryDependency[];
  requested_permissions: string[];
  required_capabilities: string[];
  required_plugins: string[];
  required_connectors: string[];
  required_models: string[];
  tags: string[];
  categories: string[];
  trust_status: RegistryTrustStatus;
  review_reference: string | null;
  released_at: string | null;
  changelog: string | null;
  deprecated: boolean;
  yanked: boolean;
  route: RegistryRoute;
  integrity: {
    sha256: string | null;
    signature_present: boolean;
    signature_key_id: string | null;
  };
}

export interface RegistryFinding {
  code: string;
  severity: string;
  message: string;
}

export interface RegistryPreview {
  id: string;
  type: "registry-preview";
  provider_id: string;
  item: RegistryItem;
  route: RegistryRoute;
  activation_allowed: boolean;
  findings: RegistryFinding[];
}

export interface RegistryActivation {
  id: string;
  type: "registry-activation";
  status: "applied";
  route: RegistryRoute;
}

export interface RegistryClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const REGISTRY_ITEMS = "registry-items";

export class RegistryClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: RegistryClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  list(query: ListQuery = {}): Promise<Page<RegistryItem>> {
    return this.collections.list<RegistryItem>(REGISTRY_ITEMS, query);
  }

  get(itemId: string, version: string): Promise<RegistryItem> {
    return this.collections.get<RegistryItem>(
      REGISTRY_ITEMS,
      `${requireText(itemId, "Registry item ID")}@${requireText(version, "Registry version")}`,
    );
  }

  preview(
    itemId: string,
    version: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<RegistryPreview> {
    return this.command<RegistryPreview>("registry.preview", itemId, version, idempotencyKey);
  }

  activate(
    itemId: string,
    version: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<RegistryActivation> {
    return this.command<RegistryActivation>("registry.activate", itemId, version, idempotencyKey);
  }

  private async command<T>(
    command: "registry.preview" | "registry.activate",
    itemId: string,
    version: string,
    idempotencyKey: string,
  ): Promise<T> {
    const resourceRef = requireText(itemId, "Registry item ID");
    const exactVersion = requireText(version, "Registry version");
    if (!idempotencyKey.trim()) throw new Error("Registry idempotency key is required");

    const headers = new Headers({
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
      "Idempotency-Key": idempotencyKey,
    });
    const payload: Record<string, JsonValue> = {
      resource_ref: resourceRef,
      version: exactVersion,
    };
    const response = await this.fetchImpl(
      `${this.baseUrl}/api/v1/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify(payload),
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

function requireText(value: string, label: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error(`${label} is required`);
  return trimmed;
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
    typeof candidate.code === "string" &&
    typeof candidate.category === "string" &&
    typeof candidate.message === "string" &&
    typeof candidate.request_id === "string" &&
    typeof candidate.correlation_id === "string" &&
    typeof candidate.retryable === "boolean"
  );
}
