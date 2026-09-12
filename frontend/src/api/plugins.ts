import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

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

export interface PluginsClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

const PLUGINS = "plugins";
const CANDIDATES = "plugin-candidates";

export class PluginsClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: PluginsClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
    this.collections = new ControlPlaneCollectionClient({ transport: this.transport });
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

  private command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, JsonValue>,
    idempotencyKey: string,
  ): Promise<T> {
    if (!idempotencyKey.trim()) throw new Error("plugin idempotency key is required");
    return this.transport.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      idempotencyKey,
      body: { resource_ref: resourceRef, ...payload },
    });
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
