import { ControlPlaneError } from "./client";
import type { APIErrorBody, JsonValue, ModelCapabilities } from "./types";

export type OnboardingState =
  | "needs_model"
  | "needs_project"
  | "needs_workspace"
  | "needs_general_assistant"
  | "needs_selection"
  | "ready_for_task";

export type OnboardingSelectionKind = "project" | "workspace" | "agent" | null;

export interface OnboardingStatus {
  id: "first-run";
  type: "onboarding_status";
  state: OnboardingState;
  authenticated_actor_present: boolean;
  project_count: number;
  workspace_count: number;
  local_model_count: number;
  self_hosted_model_count: number;
  remote_model_count: number;
  text_capable_golden_path_model_count: number;
  usable_golden_path_model_count: number;
  general_assistant_count: number;
  executable_general_assistant_count: number;
  general_assistant_blockers: Array<Record<string, JsonValue>>;
  selection_required: boolean;
  selection_kind: OnboardingSelectionKind;
  candidate_project_ids: string[];
  candidate_workspace_ids: string[];
  candidate_agent_ids: string[];
  starter_catalog_installed: boolean;
  installed_model_adapter_ids: string[];
  automatic_remote_provider_selection: false;
  automatic_paid_provider_selection: false;
  guidance: JsonValue[];
}

export type ComponentCategory =
  | "orchestrator"
  | "model_provider"
  | "executor"
  | "memory_knowledge"
  | "tools_mcp"
  | "storage"
  | "compute";

export type ComponentLifecycle = "recommended" | "supported" | "experimental" | "deprecated";
export type ComponentAvailability = "available" | "unavailable" | "installation_required";
export type ComponentCompatibilityState =
  | "compatible"
  | "compatible_with_constraints"
  | "experimental"
  | "unavailable"
  | "incompatible"
  | "installation_required"
  | "insufficient_hardware"
  | "security_blocker";
export type ComponentSetupMode = "auto" | "local" | "multi_node" | "advanced";

export interface ComponentRequirement {
  kind: "hardware" | "runtime" | "capability";
  capability: string;
  required: boolean;
  detail: string | null;
}

export interface ComponentCompatibility {
  component_id: string;
  category: ComponentCategory;
  state: ComponentCompatibilityState;
  reasons: string[];
  missing_requirements: string[];
}

export interface DiscoveredComponent {
  component_id: string;
  category: ComponentCategory;
  display_name: string;
  availability: ComponentAvailability;
  lifecycle: ComponentLifecycle;
  version: string | null;
  capabilities: string[];
  requirements: ComponentRequirement[];
  recommended_modes: ComponentSetupMode[];
  priority: number;
  source_ref: string | null;
  metadata: Record<string, JsonValue>;
  compatibility: ComponentCompatibility;
}

export interface ComponentSetupProfile {
  profile_id: string;
  mode: ComponentSetupMode;
  revision: number;
  defaults: Partial<Record<ComponentCategory, string>>;
}

export interface ComponentSetupStatus {
  id: "component-setup";
  type: "component_setup";
  components: DiscoveredComponent[];
  profiles: ComponentSetupProfile[];
  active_profile_id: string | null;
  available_setup_modes: ComponentSetupMode[];
}

export interface SaveComponentProfileInput {
  profile_id: string;
  mode: ComponentSetupMode;
  defaults?: Partial<Record<ComponentCategory, string>>;
  activate?: boolean;
}

export interface ComponentSetupProfileResult {
  id: string;
  type: "component_setup_profile";
  mode: ComponentSetupMode;
  revision: number;
  defaults: Partial<Record<ComponentCategory, string>>;
  active: boolean;
}

export interface SecretReferenceInput {
  provider: string;
  secret_id: string;
  scope: string;
  version?: string;
  metadata?: Record<string, JsonValue>;
}

export interface ConfigureOnboardingModelInput {
  adapter_id: string;
  provider_id: string;
  model_config_id: string;
  provider_model: string;
  display_name?: string;
  base_url: string;
  location: "local" | "self_hosted";
  capabilities?: Partial<ModelCapabilities>;
  credential_ref?: SecretReferenceInput;
  priority?: number;
  aliases?: string[];
}

export interface ConfigureOnboardingModelResult {
  id: string;
  type: "model";
  provider_id: string;
  adapter_id: string;
  display_name: string;
  location: "local" | "self_hosted";
  health: string;
  enabled: boolean;
  external_paid_provider_selected: false;
  credential_mode: "secret_reference" | "none";
}

export interface FirstRunTaskInput {
  objective: string;
  title?: string;
  project_id?: string;
  workspace_id?: string;
  agent_id?: string;
}

export interface FirstRunTaskResult {
  id: string;
  type: "first_run_result";
  task_id: string;
  task_status: string;
  run_id: string;
  run_status: string;
  agent_id: string;
  workspace_id: string;
  project_id: string;
  result_id: string;
  output: Record<string, JsonValue>;
}

export interface StandardAgentBootstrapResult {
  id: "standard-agent-catalog";
  type: "standard_agent_bootstrap_result";
  catalog_version: string;
  installed_agent_keys: string[];
  preserved_agent_keys: string[];
  installed_team_keys: string[];
  preserved_team_keys: string[];
  readiness: JsonValue[];
}

export interface StandardAgentCloneResult {
  id: string;
  type: "agent";
  current_revision: number;
  project_id: string | null;
  workspace_id: string | null;
  owner_ref: { type: string; id: string };
  created_at: string;
  updated_at: string;
  revision: Record<string, JsonValue>;
}

export interface OnboardingClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

/** Browser-only projection of canonical first-run and component-setup Control Plane APIs. */
export class OnboardingClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: OnboardingClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  status(): Promise<OnboardingStatus> {
    return this.request<OnboardingStatus>("/onboarding/first-run");
  }

  componentSetup(): Promise<ComponentSetupStatus> {
    return this.request<ComponentSetupStatus>("/component-setup/component-setup");
  }

  saveComponentProfile(input: SaveComponentProfileInput): Promise<ComponentSetupProfileResult> {
    return this.command<ComponentSetupProfileResult>("/commands/onboarding.save-component-profile", {
      resource_ref: "component-setup",
      ...input,
    });
  }

  selectComponentProfile(profileId: string): Promise<ComponentSetupProfileResult> {
    return this.command<ComponentSetupProfileResult>(
      "/commands/onboarding.select-component-profile",
      {
        resource_ref: "component-setup",
        profile_id: profileId,
      },
    );
  }

  configureModel(input: ConfigureOnboardingModelInput): Promise<ConfigureOnboardingModelResult> {
    return this.command<ConfigureOnboardingModelResult>("/commands/onboarding.configure-model", {
      resource_ref: "first-run",
      ...input,
    });
  }

  bootstrapStandardAgents(): Promise<StandardAgentBootstrapResult> {
    return this.command<StandardAgentBootstrapResult>("/commands/standard-agent.bootstrap", {
      resource_ref: "standard-agent-catalog",
    });
  }

  cloneGeneralAssistant(input: {
    project_id: string;
    workspace_id: string;
    name?: string;
  }): Promise<StandardAgentCloneResult> {
    return this.command<StandardAgentCloneResult>("/commands/standard-agent.clone", {
      resource_ref: "general_assistant",
      ...input,
    });
  }

  runFirstTask(input: FirstRunTaskInput): Promise<FirstRunTaskResult> {
    return this.command<FirstRunTaskResult>("/commands/onboarding.run-first-task", {
      resource_ref: "first-run",
      ...input,
    });
  }

  private command<T>(path: string, body: Record<string, unknown>): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body: stripUndefined(body),
      idempotencyKey: crypto.randomUUID(),
    });
  }

  private async request<T>(
    path: string,
    options: { method?: string; body?: unknown; idempotencyKey?: string } = {},
  ): Promise<T> {
    const headers = new Headers({
      Accept: "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
    });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);

    const response = await this.fetchImpl(`${this.baseUrl}/api/v1${path}`, {
      method: options.method ?? "GET",
      headers,
      credentials: "include",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    const text = await response.text();
    const payload: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, payload));
    }
    return payload as T;
  }
}

function stripUndefined(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined));
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
