import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue } from "./types";

export type AutomationState = "enabled" | "paused" | "disabled" | "invalid";
export type AutomationTriggerType =
  | "one_time"
  | "recurring"
  | "webhook"
  | "platform_event"
  | "manual";
export type AutomationDeliveryStatus =
  | "pending"
  | "processing"
  | "succeeded"
  | "deduplicated"
  | "failed"
  | "rejected";
export type AutomationOverlapPolicy = "allow" | "skip_while_processing";
export type MissedSchedulePolicy = "coalesce" | "skip";

export interface CanonicalOwnerRef {
  type: string;
  id: string;
}

export interface CanonicalAutomationIdentity {
  principal_ref: string;
  owner_type: string;
  owner_id: string;
}

export interface CanonicalAutomationTrigger {
  type: AutomationTriggerType;
  timezone: string;
  at: string | null;
  interval_seconds: number | null;
  event_type: string | null;
  filters: Record<string, JsonValue>;
  webhook_source: string | null;
  verification_ref: string | null;
  missed_schedule_policy: MissedSchedulePolicy;
}

export interface CanonicalAutomationTaskTemplate {
  title: string;
  objective: string;
  project_id: string | null;
  workspace_id: string | null;
  payload: Record<string, JsonValue>;
}

export interface CanonicalAutomationRetryPolicy {
  max_attempts: number;
  base_backoff_seconds: number;
}

export interface CanonicalAutomation {
  id: string;
  type: "automation";
  name: string;
  description: string;
  project_id: string | null;
  workspace_id: string | null;
  state: AutomationState;
  identity: CanonicalAutomationIdentity;
  owner_ref: CanonicalOwnerRef;
  trigger: CanonicalAutomationTrigger;
  task_template: CanonicalAutomationTaskTemplate;
  deduplication_strategy: "delivery_key";
  retry_policy: CanonicalAutomationRetryPolicy;
  overlap_policy: AutomationOverlapPolicy;
  created_at: string;
  updated_at: string;
  revision: number;
  last_evaluated_at: string | null;
  next_evaluation_at: string | null;
}

export interface CanonicalAutomationDelivery {
  id: string;
  type: "automation-delivery";
  automation_id: string;
  trigger_type: AutomationTriggerType;
  source: string;
  dedupe_key: string;
  fired_at: string;
  received_at: string;
  payload: Record<string, JsonValue>;
  status: AutomationDeliveryStatus;
  attempt: number;
  generated_task_id: string | null;
  error_code: string | null;
  error_message: string | null;
  processing_duration_ms: number | null;
  owner_ref: CanonicalOwnerRef;
  project_id: string | null;
  workspace_id: string | null;
}

export interface AutomationTriggerInput {
  type: AutomationTriggerType;
  timezone?: string;
  at?: string;
  interval_seconds?: number;
  event_type?: string;
  filters?: Record<string, JsonValue>;
  webhook_source?: string;
  verification_ref?: string;
  missed_schedule_policy?: MissedSchedulePolicy;
}

export interface AutomationTaskTemplateInput {
  title: string;
  objective: string;
  project_id?: string;
  workspace_id?: string;
  payload?: Record<string, JsonValue>;
}

export interface CreateAutomationInput {
  name: string;
  description?: string;
  project_id?: string;
  workspace_id?: string;
  trigger: AutomationTriggerInput;
  task_template: AutomationTaskTemplateInput;
  deduplication_strategy?: "delivery_key";
  retry_policy?: CanonicalAutomationRetryPolicy;
  overlap_policy?: AutomationOverlapPolicy;
}

export interface UpdateAutomationInput {
  name?: string;
  description?: string;
  trigger?: AutomationTriggerInput;
  task_template?: AutomationTaskTemplateInput;
  deduplication_strategy?: "delivery_key";
  retry_policy?: CanonicalAutomationRetryPolicy;
  overlap_policy?: AutomationOverlapPolicy;
}

export interface AutomationClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export class AutomationClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;

  constructor(options: AutomationClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
  }

  create(input: CreateAutomationInput): Promise<CanonicalAutomation> {
    return this.command<CanonicalAutomation>("automation.create", "automations", input);
  }

  update(automationId: string, input: UpdateAutomationInput): Promise<CanonicalAutomation> {
    const normalized = { ...input };
    if (normalized.description !== undefined && !normalized.description.trim()) {
      delete normalized.description;
    }
    return this.command<CanonicalAutomation>("automation.update", automationId, normalized);
  }

  pause(automationId: string): Promise<CanonicalAutomation> {
    return this.command<CanonicalAutomation>("automation.pause", automationId);
  }

  resume(automationId: string): Promise<CanonicalAutomation> {
    return this.command<CanonicalAutomation>("automation.resume", automationId);
  }

  disable(automationId: string): Promise<CanonicalAutomation> {
    return this.command<CanonicalAutomation>("automation.disable", automationId);
  }

  test(
    automationId: string,
    payload: Record<string, JsonValue> = {},
  ): Promise<CanonicalAutomationDelivery> {
    return this.command<CanonicalAutomationDelivery>("automation.test", automationId, {
      payload,
    });
  }

  retryDelivery(deliveryId: string): Promise<CanonicalAutomationDelivery> {
    return this.command<CanonicalAutomationDelivery>("automation.retry-delivery", deliveryId);
  }

  private command<T>(
    command: string,
    resourceRef: string,
    payload: object = {},
  ): Promise<T> {
    return this.transport.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      body: { resource_ref: resourceRef, ...payload },
      idempotencyKey: crypto.randomUUID(),
    });
  }
}
