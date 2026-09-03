import type { TelemetryTimelineEntry, TimelineItem } from "./types";

export interface TimelineSummary {
  total: number;
  domainEvents: number;
  telemetryEntries: number;
  failures: number;
  components: string[];
}

export function isTelemetryEntry(item: TimelineItem): item is TelemetryTimelineEntry {
  return item.type === "telemetry";
}

export function summarizeTimeline(items: TimelineItem[]): TimelineSummary {
  const telemetry = items.filter(isTelemetryEntry);
  return {
    total: items.length,
    domainEvents: items.length - telemetry.length,
    telemetryEntries: telemetry.length,
    failures: telemetry.filter(isFailureTelemetry).length,
    components: Array.from(new Set(telemetry.map((item) => item.component))).sort(),
  };
}

export function timelineName(item: TimelineItem): string {
  return isTelemetryEntry(item) ? item.event_name : item.event_type;
}

export function timelineTimestamp(item: TimelineItem): string {
  return isTelemetryEntry(item) ? item.timestamp : item.occurred_at;
}

export function timelineContext(item: TimelineItem): string {
  return isTelemetryEntry(item) ? item.component : `${item.subject_type}:${item.subject_id}`;
}

function isFailureTelemetry(item: TelemetryTimelineEntry): boolean {
  if (item.failure !== null) return true;
  return ["failed", "failure", "error"].includes(item.outcome.toLowerCase());
}
