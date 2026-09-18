import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

export type KnownRegistryItemType =
  | "agent"
  | "agent_team"
  | "tool"
  | "skill"
  | "plugin"
  | "workflow"
  | "template"
  | "model_configuration"
  | "connector"
  | "application"
  | "evaluation"
  | "documentation";

/**
 * Marketplace kinds are intentionally open-ended. Known values are documented by
 * KnownRegistryItemType, but future server-registered kinds must remain renderable.
 */
export type RegistryItemType = string;

export type RegistryTrustStatus = "untrusted" | "reviewed" | "trusted" | "local";
export type RegistryRoute = "plugin" | "portable_import" | "kind_handler" | "manual";

export interface RegistryManifestReference {
  kind: string;
  reference: string;
  schema_version: string | null;
}

export interface RegistryDependency {
  item_id: string;
  minimum_version: string | null;
  maximum_version: string | null;
  optional: boolean;
  item_kind?: string | null;
  kind?: string | null;
  status?: "satisfied" | "missing" | "incompatible" | "unknown";
  installed_version?: string | null;
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

export interface RegistryCompatibility {
  minimum_platform_version?: string | null;
  maximum_platform_version?: string | null;
  compatible?: boolean | null;
  platform_compatible?: boolean | null;
  operating_system_compatible?: boolean | null;
  architecture_compatible?: boolean | null;
  operating_systems?: string[];
  architectures?: string[];
  required_runtimes?: string[];
  missing_runtimes?: string[];
  missing_capabilities?: string[];
  missing_plugins?: string[];
  missing_connectors?: string[];
  missing_models?: string[];
  reasons?: string[];
  requirements?: string[];
}

export interface RegistryOwnerExtension {
  handler_available: boolean | null;
  requirements: JsonValue | null;
  details: JsonValue | null;
  status: JsonValue | null;
  supported_operations?: Array<"install" | "update" | "uninstall"> | null;
}

export interface RegistryUpdateState {
  installed: boolean;
  installed_version: string | null;
  candidate_version: string;
  pinned_version: string | null;
  update_available: boolean;
}

export interface RegistryKindDescriptor {
  id?: string;
  type?: "marketplace-kind";
  kind: string;
  display_name: string;
  default_route: RegistryRoute;
  supports_install: boolean;
  supports_update: boolean;
  supports_uninstall: boolean;
  owner_resource?: string | null;
  management_path?: string | null;
}

export interface RegistryItem {
  id: string;
  type: "registry-item";
  item_id: string;
  item_type: RegistryItemType;
  kind?: string;
  name: string;
  description: string;
  version: string;
  publisher: string;
  source: {
    repository: string;
    package_reference: string | null;
    revision: string | null;
    registry?: string | null;
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
  trust?: RegistryTrustStatus;
  review_reference: string | null;
  released_at: string | null;
  changelog: string | null;
  deprecated: boolean;
  yanked: boolean;
  route: RegistryRoute;
  route_available?: boolean | null;
  integrity: {
    sha256: string | null;
    signature_present: boolean;
    signature_key_id: string | null;
  };
  manifest?: RegistryManifestReference | null;
  manifest_reference?: RegistryManifestReference | null;
  compatible?: boolean;
  compatibility?: RegistryCompatibility | null;
  operation_state?: "ready" | "blocked" | "manual" | "unsupported" | "missing_handler" | null;
  installed: boolean;
  installed_version: string | null;
  pinned_version: string | null;
  update_available: boolean;
  installation: RegistryInstallation | null;
  update_state?: RegistryUpdateState | null;
  owner_extension?: RegistryOwnerExtension | null;
  kind_details?: Record<string, JsonValue> | null;
}

export interface RegistryFinding {
  code: string;
  severity: "warning" | "error";
  message: string;
}

export interface RegistryPermissionChange {
  permission: string;
  change: "added" | "removed" | "changed";
  previous?: string | null;
  next?: string | null;
}

export interface MarketplaceDependencyResolution {
  required_by: string;
  item_id: string;
  item_kind: string | null;
  optional: boolean;
  minimum_version: string | null;
  maximum_version: string | null;
  status: string;
  installed_version: string | null;
  candidate_version: string | null;
  candidate_kind: string | null;
  candidate_source_registry: string | null;
  path: string[];
}

export interface MarketplaceCompatibilityDecision {
  compatible: boolean;
  platform_compatible: boolean;
  operating_system_compatible: boolean;
  architecture_compatible: boolean;
  missing_runtimes: string[];
  missing_capabilities: string[];
  missing_plugins: string[];
  missing_connectors: string[];
  missing_models: string[];
}

export interface MarketplacePermissionDiff {
  previous: string[];
  requested: string[];
  added: string[];
  removed: string[];
  unchanged: string[];
  changed: boolean;
  escalated: boolean;
}

export interface MarketplaceProvenanceDiff {
  installed: boolean;
  previous_source_registry: string | null;
  candidate_source_registry: string | null;
  previous_publisher: string | null;
  candidate_publisher: string;
  previous_repository: string | null;
  candidate_repository: string;
  previous_package_reference: string | null;
  candidate_package_reference: string;
  previous_revision: string | null;
  candidate_revision: string | null;
  previous_artifact_sha256: string | null;
  candidate_artifact_sha256: string;
  previous_signature?: string | null;
  candidate_signature?: string | null;
  previous_signature_key_id: string | null;
  candidate_signature_key_id: string | null;
  previous_trust_status: RegistryTrustStatus | null;
  candidate_trust_status: RegistryTrustStatus;
  previous_review_reference: string | null;
  candidate_review_reference: string | null;
  source_changed: boolean;
  publisher_changed: boolean;
  repository_changed: boolean;
  package_reference_changed: boolean;
  revision_changed: boolean;
  artifact_changed: boolean;
  signature_changed: boolean;
  signature_key_changed: boolean;
  trust_changed: boolean;
  trust_downgraded: boolean;
  review_changed: boolean;
  changed: boolean;
}

export interface MarketplaceApprovalRequirement {
  required: boolean;
  reasons: string[];
  authorization_required: boolean;
}

export interface MarketplaceDecisionUpdateState {
  installed_version: string | null;
  candidate_version: string;
  latest_compatible_version: string | null;
  update_available: boolean;
  pinned: boolean;
  blocked_by_pin: boolean;
  incompatible_update: boolean;
  current_yanked: boolean;
  candidate_yanked: boolean;
  candidate_deprecated: boolean;
  source_change: boolean;
  permission_change: boolean;
  trust_integrity_issue: boolean;
}

export interface MarketplaceDecision {
  operation: "install" | "update" | "uninstall";
  dependencies: MarketplaceDependencyResolution[];
  compatibility: MarketplaceCompatibilityDecision;
  permission_diff: MarketplacePermissionDiff;
  provenance_diff: MarketplaceProvenanceDiff;
  approval: MarketplaceApprovalRequirement;
  update_state: MarketplaceDecisionUpdateState;
  dependency_blocked: boolean;
}

export interface RegistryPreview {
  id: string;
  type: "registry-preview";
  provider_id: string;
  artifact_sha256: string;
  item: RegistryItem;
  route: RegistryRoute;
  activation_allowed: boolean;
  findings: RegistryFinding[];
  dependencies?: RegistryDependency[];
  missing_requirements?: string[];
  incompatible_requirements?: string[];
  permission_changes?: RegistryPermissionChange[];
  source_changed?: boolean;
  publisher_changed?: boolean;
  security_notices?: string[];
  integrity_notices?: string[];
  approval_required?: boolean;
  approval_reference?: string | null;
  blockers?: string[];
  decision?: MarketplaceDecision | null;
  kind_details?: Record<string, JsonValue> | null;
}

export interface MarketplaceMutation {
  id: string;
  type: "marketplace-mutation";
  action: "install" | "update" | "uninstall";
  status: "applied";
  route: RegistryRoute;
  decision: MarketplaceDecision;
  installation: RegistryInstallation | null;
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
  | "registry.activate"
  | "registry.pin"
  | "registry.unpin"
  | "marketplace.preview"
  | "marketplace.install"
  | "marketplace.update"
  | "marketplace.uninstall";

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

  async preview(
    itemId: string,
    version: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<RegistryPreview> {
    return this.command<RegistryPreview>(
      "marketplace.preview",
      itemId,
      { version: requireText(version, "Registry version") },
      idempotencyKey,
    );
  }

  async install(
    itemId: string,
    version: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<MarketplaceMutation> {
    return this.command<MarketplaceMutation>(
      "marketplace.install",
      itemId,
      { version: requireText(version, "Registry version") },
      idempotencyKey,
    );
  }

  async update(
    itemId: string,
    version: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<MarketplaceMutation> {
    return this.command<MarketplaceMutation>(
      "marketplace.update",
      itemId,
      { version: requireText(version, "Registry version") },
      idempotencyKey,
    );
  }

  async activate(
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

  async uninstall(
    itemId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<MarketplaceMutation> {
    return this.command<MarketplaceMutation>(
      "marketplace.uninstall",
      itemId,
      {},
      idempotencyKey,
    );
  }

  async pin(
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

  async unpin(
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

  private command<T>(
    command: RegistryCommand,
    itemId: string,
    commandPayload: Record<string, JsonValue>,
    idempotencyKey: string,
  ): Promise<T> {
    const resourceRef = requireText(itemId, "Registry item ID");
    if (!idempotencyKey.trim()) throw new Error("Registry idempotency key is required");

    const payload: Record<string, JsonValue> = {
      resource_ref: resourceRef,
      ...commandPayload,
    };
    return this.transport.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      body: payload,
      idempotencyKey,
    });
  }
}

function requireText(value: string, label: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error(`${label} is required`);
  return trimmed;
}
