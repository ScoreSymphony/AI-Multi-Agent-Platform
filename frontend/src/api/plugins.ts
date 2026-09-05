import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export interface PluginManifestDocument {
  plugin_id: string;
  name: string;
  description: string;
  plugin_version: string;
  manifest_version: string;
  author: string;
  provenance: Record<string, JsonValue>;
  supported_platform: Record<string, JsonValue>;
  extensions: JsonValue[];
  capabilities: string[];
  requested_permissions: string[];
  configuration_version: string;
  configuration_schema: Record<string, JsonValue>;
  dependencies: JsonValue[];
  optional_external_services: string[];
  state_version: string;
  state_migrations: JsonValue[];
  ui_metadata: Record<string, JsonValue>;
}

export interface CanonicalPlugin {
  id: string;
  type: "plugin" | string;
  name: string;
  description: string;
  author: string;
  plugin_version: string;
  manifest_version: string;
  state: string;
  compatibility: string;
  health: string;
  health_detail: string | null;
  configured: boolean;
  configuration_version: string;
  state_version: string;
  capabilities: string[];
  extension_ids: string[];
  extension_types: string[];
  requested_permissions: string[];
  granted_permissions: string[];
  dependencies: string[];
  install_source: string;
  provenance_source: string;
  provenance_license: string;
  manifest_digest: string;
  manifest: PluginManifestDocument;
}

export interface CanonicalPluginCandidate {
  id: string;
  type: "plugin-candidate" | string;
  name: string;
  description: string;
  author: string;
  plugin_version: string;
  manifest_version: string;
  install_source: string;
  capabilities: string[];
  requested_permissions: string[];
  extension_ids: string[];
  extension_types: string[];
  manifest_digest: string;
  manifest: PluginManifestDocument;
}

export interface PluginUpdateValidation {
  id: string;
  type: "plugin-update-validation" | string;
  compatible: boolean;
  current_version: string;
  candidate_version: string;
  manifest_digest: string;
}

export interface PluginRemoval {
  id: string;
  type: "plugin-removal" | string;
  removed: boolean;
  plugin_version: string;
}

export interface PluginsClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const PLUGINS = "plugins";
const CANDIDATES = "plugin-candidates";

export class PluginsClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: PluginsClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  listPlugins(query: ListQuery = {}): Promise<Page<CanonicalPlugin>> {
    return this.collections.list<CanonicalPlugin>(PLUGINS, query);
  }

  getPlugin(pluginId: string): Promise<CanonicalPlugin> {
    return this.collections.get<CanonicalPlugin>(PLUGINS, requireRef(pluginId, "plugin"));
  }

  listCandidates(query: ListQuery = {}): Promise<Page<CanonicalPluginCandidate>> {
    return this.collections.list<CanonicalPluginCandidate>(CANDIDATES, query);
  }

  getCandidate(pluginId: string): Promise<CanonicalPluginCandidate> {
    return this.collections.get<CanonicalPluginCandidate>(
      CANDIDATES,
      requireRef(pluginId, "plugin candidate"),
    );
  }

  install(
    pluginId: string,
    manifestDigest: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalPlugin> {
    return this.command<CanonicalPlugin>(
      "plugin.install",
      requireRef(pluginId, "plugin candidate"),
      { manifest_digest: requireDigest(manifestDigest) },
      idempotencyKey,
    );
  }

  configure(
    pluginId: string,
    configuration: Record<string, JsonValue>,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalPlugin> {
    return this.command<CanonicalPlugin>(
      "plugin.configure",
      requireRef(pluginId, "plugin"),
      { configuration },
      idempotencyKey,
    );
  }

  enable(
    pluginId: string,
    manifestDigest: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalPlugin> {
    return this.command<CanonicalPlugin>(
      "plugin.enable",
      requireRef(pluginId, "plugin"),
      { manifest_digest: requireDigest(manifestDigest) },
      idempotencyKey,
    );
  }

  disable(
    pluginId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalPlugin> {
    return this.command<CanonicalPlugin>(
      "plugin.disable",
      requireRef(pluginId, "plugin"),
      {},
      idempotencyKey,
    );
  }

  refreshHealth(
    pluginId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalPlugin> {
    return this.command<CanonicalPlugin>(
      "plugin.refresh-health",
      requireRef(pluginId, "plugin"),
      {},
      idempotencyKey,
    );
  }

  validateUpdate(
    pluginId: string,
    candidateManifestDigest: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<PluginUpdateValidation> {
    return this.command<PluginUpdateValidation>(
      "plugin.validate-update",
      requireRef(pluginId, "plugin"),
      { manifest_digest: requireDigest(candidateManifestDigest) },
      idempotencyKey,
    );
  }

  remove(
    pluginId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<PluginRemoval> {
    return this.command<PluginRemoval>(
      "plugin.remove",
      requireRef(pluginId, "plugin"),
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
    if (!idempotencyKey.trim()) throw new Error("plugin idempotency key is required");
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

function requireRef(value: string, label: string): string {
  if (!value.trim()) throw new Error(`${label} reference is required`);
  return value;
}

function requireDigest(value: string): string {
  const digest = value.trim();
  if (!digest) throw new Error("plugin manifest digest is required");
  return digest;
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
