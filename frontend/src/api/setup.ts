import { ApiTransport } from "./transport";
import type { ApiRequestOptions, ApiTransportOptions } from "./transport";

export type SetupStepId =
  | "identity"
  | "environment"
  | "components"
  | "configuration"
  | "validation"
  | "ready";

export type SetupStepState = "pending" | "current" | "complete" | "blocked";
export type SetupActionKind = "reuse" | "install" | "configure" | "activate" | "validate" | "manual";
export type SetupActionState = "pending" | "completed" | "failed" | "blocked" | "manual_required";
export type SetupInstallStatus =
  | "installed"
  | "installable"
  | "manual_required"
  | "blocked";

export interface SetupStepProjection {
  id: SetupStepId;
  state: SetupStepState;
}

export interface SetupProductCard {
  id: string;
  kind: "component" | "registry_item";
  display_name: string;
  utility: string;
  category: string;
  install_status: SetupInstallStatus;
  compatibility: string;
  recommendation: string;
  dependencies: string[];
  blockers: string[];
  delivery: "local" | "external" | string;
  requires_secrets: boolean;
  configuration_fields: string[];
  license: string | null;
  upstream: string | null;
  version: string | null;
  technical_id: string;
}

export interface SetupRegistrySelection {
  item_id: string;
  version: string;
}

export interface SetupPlanAction {
  action_id: string;
  kind: SetupActionKind;
  state: SetupActionState;
  component_ref: string;
  display_name: string;
  category: string;
  dependencies: string[];
  blockers: string[];
  owner: string;
  version: string | null;
}

export interface SetupSessionStatus {
  id: "initial-setup";
  type: "setup_session";
  current_step: SetupStepId;
  steps: SetupStepProjection[];
  catalog: SetupProductCard[];
  registry_items: SetupRegistrySelection[];
  active_profile_id: string | null;
  plan: {
    actions: SetupPlanAction[];
    blocking: boolean;
    mutation_required: boolean;
  };
  readiness: {
    ready: boolean;
    dashboard_allowed: boolean;
    canonical_onboarding_state: string | null;
    blocking_actions: string[];
  };
  updated_at: string;
}

export interface SetupProvisioningOutcome {
  action_id: string;
  state: SetupActionState;
  idempotency_key: string;
  updated_at: string;
  error_code: string | null;
  error_message: string | null;
}

export interface SetupProvisioningResult {
  id: string;
  type: "provisioning_operation";
  replayed: boolean;
  outcome: SetupProvisioningOutcome | null;
  setup: SetupSessionStatus;
}

export interface SetupClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

/** Browser projection of the persistent server-owned initial setup lifecycle. */
export class SetupClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;

  constructor(options: SetupClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
  }

  status(): Promise<SetupSessionStatus> {
    return this.request<SetupSessionStatus>("/setup-sessions/initial-setup");
  }

  update(input: {
    current_step?: Exclude<SetupStepId, "identity" | "ready">;
    registry_items?: SetupRegistrySelection[];
  }): Promise<SetupSessionStatus> {
    return this.command<SetupSessionStatus>("/commands/onboarding.update-setup-session", {
      resource_ref: "initial-setup",
      ...input,
    });
  }

  provision(actionIds: string[] = []): Promise<SetupProvisioningResult> {
    return this.command<SetupProvisioningResult>("/commands/onboarding.provision-setup", {
      resource_ref: "initial-setup",
      ...(actionIds.length > 0 ? { action_ids: actionIds } : {}),
    });
  }

  validate(): Promise<SetupSessionStatus> {
    return this.command<SetupSessionStatus>("/commands/onboarding.validate-setup", {
      resource_ref: "initial-setup",
    });
  }

  private command<T>(path: string, body: Record<string, unknown>): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body,
      idempotencyKey: crypto.randomUUID(),
    });
  }

  private request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
    return this.transport.request<T>(path, options);
  }
}
