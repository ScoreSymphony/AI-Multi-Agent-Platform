import type {
  AutomationOverlapPolicy,
  AutomationTriggerInput,
  AutomationTriggerType,
  CanonicalAutomation,
  CreateAutomationInput,
  MissedSchedulePolicy,
  UpdateAutomationInput,
} from "../../api/automations";
import type { JsonValue } from "../../api/types";

export interface AutomationDraft {
  name: string;
  description: string;
  projectId: string;
  workspaceId: string;
  triggerType: AutomationTriggerType;
  timezone: string;
  at: string;
  intervalSeconds: string;
  eventType: string;
  filtersJson: string;
  webhookSource: string;
  verificationRef: string;
  missedSchedulePolicy: MissedSchedulePolicy;
  taskTitle: string;
  taskObjective: string;
  taskProjectId: string;
  taskWorkspaceId: string;
  taskPayloadJson: string;
  retryMaxAttempts: string;
  retryBaseBackoffSeconds: string;
  overlapPolicy: AutomationOverlapPolicy;
}

export function draftFromAutomation(automation?: CanonicalAutomation): AutomationDraft {
  return {
    name: automation?.name ?? "",
    description: automation?.description ?? "",
    projectId: automation?.project_id ?? "",
    workspaceId: automation?.workspace_id ?? "",
    triggerType: automation?.trigger.type ?? "manual",
    timezone: automation?.trigger.timezone ?? "UTC",
    at: automation?.trigger.at ?? "",
    intervalSeconds: automation?.trigger.interval_seconds?.toString() ?? "",
    eventType: automation?.trigger.event_type ?? "",
    filtersJson: JSON.stringify(automation?.trigger.filters ?? {}, null, 2),
    webhookSource: automation?.trigger.webhook_source ?? "",
    verificationRef: automation?.trigger.verification_ref ?? "",
    missedSchedulePolicy: automation?.trigger.missed_schedule_policy ?? "coalesce",
    taskTitle: automation?.task_template.title ?? "",
    taskObjective: automation?.task_template.objective ?? "",
    taskProjectId: automation?.task_template.project_id ?? "",
    taskWorkspaceId: automation?.task_template.workspace_id ?? "",
    taskPayloadJson: JSON.stringify(automation?.task_template.payload ?? {}, null, 2),
    retryMaxAttempts: automation?.retry_policy.max_attempts.toString() ?? "3",
    retryBaseBackoffSeconds: automation?.retry_policy.base_backoff_seconds.toString() ?? "1",
    overlapPolicy: automation?.overlap_policy ?? "skip_while_processing",
  };
}

export function validateAutomationDraft(draft: AutomationDraft): void {
  if (!draft.name.trim()) throw new Error("name is required");
  if (!draft.taskTitle.trim()) throw new Error("task title is required");
  if (!draft.taskObjective.trim()) throw new Error("task objective is required");
  if (!draft.timezone.trim()) throw new Error("timezone is required");
  if ((draft.triggerType === "one_time" || draft.triggerType === "recurring") && !draft.at.trim()) {
    throw new Error("scheduled triggers require an ISO-8601 at timestamp");
  }
  if (draft.triggerType === "recurring") {
    const interval = Number(draft.intervalSeconds);
    if (!Number.isFinite(interval) || interval <= 0) throw new Error("recurring interval must be positive");
  }
  if (draft.triggerType === "platform_event" && !draft.eventType.trim()) {
    throw new Error("platform-event triggers require an event type");
  }
  if (draft.triggerType === "webhook" && !draft.webhookSource.trim()) {
    throw new Error("webhook triggers require a source");
  }
  const attempts = Number(draft.retryMaxAttempts);
  if (!Number.isInteger(attempts) || attempts < 1) throw new Error("retry attempts must be an integer of at least 1");
  const backoff = Number(draft.retryBaseBackoffSeconds);
  if (!Number.isFinite(backoff) || backoff < 0) throw new Error("base backoff must be non-negative");
  parseJsonObject(draft.filtersJson, "trigger filters");
  parseJsonObject(draft.taskPayloadJson, "task payload");
}

export function toCreateInput(draft: AutomationDraft): CreateAutomationInput {
  return {
    name: draft.name.trim(),
    description: optional(draft.description),
    project_id: optional(draft.projectId),
    workspace_id: optional(draft.workspaceId),
    trigger: triggerInput(draft),
    task_template: {
      title: draft.taskTitle.trim(),
      objective: draft.taskObjective.trim(),
      project_id: optional(draft.taskProjectId),
      workspace_id: optional(draft.taskWorkspaceId),
      payload: parseJsonObject(draft.taskPayloadJson, "task payload"),
    },
    deduplication_strategy: "delivery_key",
    retry_policy: {
      max_attempts: Number(draft.retryMaxAttempts),
      base_backoff_seconds: Number(draft.retryBaseBackoffSeconds),
    },
    overlap_policy: draft.overlapPolicy,
  };
}

export function toUpdateInput(draft: AutomationDraft): UpdateAutomationInput {
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || "",
    trigger: triggerInput(draft),
    task_template: {
      title: draft.taskTitle.trim(),
      objective: draft.taskObjective.trim(),
      project_id: optional(draft.taskProjectId),
      workspace_id: optional(draft.taskWorkspaceId),
      payload: parseJsonObject(draft.taskPayloadJson, "task payload"),
    },
    deduplication_strategy: "delivery_key",
    retry_policy: {
      max_attempts: Number(draft.retryMaxAttempts),
      base_backoff_seconds: Number(draft.retryBaseBackoffSeconds),
    },
    overlap_policy: draft.overlapPolicy,
  };
}

export function parseJsonObject(value: string, label: string): Record<string, JsonValue> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value || "{}");
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return parsed as Record<string, JsonValue>;
}

function triggerInput(draft: AutomationDraft): AutomationTriggerInput {
  const trigger: AutomationTriggerInput = {
    type: draft.triggerType,
    timezone: draft.timezone.trim(),
    filters: parseJsonObject(draft.filtersJson, "trigger filters"),
  };
  if (draft.triggerType === "one_time" || draft.triggerType === "recurring") {
    trigger.at = draft.at.trim();
    trigger.missed_schedule_policy = draft.missedSchedulePolicy;
  }
  if (draft.triggerType === "recurring") trigger.interval_seconds = Number(draft.intervalSeconds);
  if (draft.triggerType === "platform_event") trigger.event_type = draft.eventType.trim();
  if (draft.triggerType === "webhook") {
    trigger.webhook_source = draft.webhookSource.trim();
    const verificationRef = optional(draft.verificationRef);
    if (verificationRef) trigger.verification_ref = verificationRef;
  }
  return trigger;
}

function optional(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}
