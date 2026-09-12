import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

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

export interface RegistryInstallationHistory {
  version: string;
  source_registry: string;
  source_repository: string;
  package_reference: string;
  revision: string | null;
  license: string;
  provenance: string;
}

export interface RegistryInstallation {
  id: string;
  type: "registry-installation";
  item_id: string;
  version: string;
  pinned_version: string | null;
  source_registry: string;
  source_repository: string;
  package_reference: string;
  revision: string | null;
  license: string;
  provenance: string;
  history: RegistryInstallationHistory[];
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
  installed: boolean;
  installed_version: string | null;
  pinned_version: string | null;
  update_available: boolean;
  installation: RegistryInstallation | null;
}

export interface RegistryFinding {
  code: string;
  severity: "warning" | "error";
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
  installation: RegistryInstallation | null;
}

export interface RegistryClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

const REGISTRY_ITEMS = "registry-items";

type RegistryCommand =
  | "registry.preview"
  | "registry.activate"
  | "registry.pin"
  | "registry.unpin";

export class RegistryClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: RegistryClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
    this.collections = new ControlPlaneCollectionClient({ transport: this.transport });
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
    return this.command<RegistryPreview>(
      "registry.preview",
      itemId,
      { version: requireText(version, "Registry version") },
      idempotencyKey,
    );
  }

  activate(
    itemId: string,
    version: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<RegistryActivation> {
    return this.command<RegistryActivation>(
      "registry.activate",
      itemId,
      { version: requireText(version, "Registry version") },
      idempotencyKey,
    );
  }

  pin(
    itemId: string,
    version: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<RegistryInstallation> {
    return this.command<RegistryInstallation>(
      "registry.pin",
      itemId,
      { version: requireText(version, "Registry version") },
      idempotencyKey,
    );
  }

  unpin(
    itemId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<RegistryInstallation> {
    return this.command<RegistryInstallation>(
      "registry.unpin",
      itemId,
      {},
      idempotencyKey,
    );
  }

  private async command<T>(
    command: RegistryCommand,
    itemId: string,
    commandPayload: Record<string, JsonValue>,
    idempotencyKey: string,
  ): Promise<T> {
    const resourceRef = requireText(itemId, "Registry item ID");
    if (!idempotencyKey.trim()) throw new Error("Registry idempotency key is required");
    return this.transport.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      idempotencyKey,
      body: { resource_ref: resourceRef, ...commandPayload },
    });
  }
}

function requireText(value: string, label: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error(`${label} is required`);
  return trimmed;
}
