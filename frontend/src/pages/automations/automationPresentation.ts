import type { CanonicalAutomation } from "../../api/automations";

export function formatAutomationDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export function triggerSummary(automation: CanonicalAutomation): string {
  const trigger = automation.trigger;
  if (trigger.type === "recurring") return `recurring · ${trigger.interval_seconds ?? "?"}s`;
  if (trigger.type === "one_time") return `one time · ${formatAutomationDate(trigger.at)}`;
  if (trigger.type === "platform_event") return `event · ${trigger.event_type ?? "—"}`;
  if (trigger.type === "webhook") return `webhook · ${trigger.webhook_source ?? "—"}`;
  return "manual";
}
