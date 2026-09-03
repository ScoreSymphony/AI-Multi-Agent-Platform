import { describe, expect, it } from "vitest";
import { summarizeTimeline, timelineContext, timelineName, timelineTimestamp } from "./observability";
import type { TimelineItem } from "./types";

const items: TimelineItem[] = [
  {
    id: "event_123e4567-e89b-42d3-a456-426614174020",
    type: "event",
    schema_version: "1",
    event_type: "task.created",
    occurred_at: "2026-09-03T02:00:00+00:00",
    subject_type: "task",
    subject_id: "task_123e4567-e89b-42d3-a456-426614174000",
    correlation_id: "correlation_test",
    payload: {},
  },
  {
    id: "telemetry_123e4567-e89b-42d3-a456-426614174021",
    type: "telemetry",
    event_name: "executor.completed",
    component: "executor",
    timestamp: "2026-09-03T02:00:01+00:00",
    outcome: "success",
    duration_seconds: 0.25,
    failure: null,
    context: {},
    attributes: {},
  },
  {
    id: "telemetry_123e4567-e89b-42d3-a456-426614174022",
    type: "telemetry",
    event_name: "model.failed",
    component: "model",
    timestamp: "2026-09-03T02:00:02+00:00",
    outcome: "failed",
    duration_seconds: 1.5,
    failure: { code: "provider_error" },
    context: {},
    attributes: {},
  },
];

describe("task timeline observability helpers", () => {
  it("keeps domain events and derived telemetry distinct", () => {
    expect(summarizeTimeline(items)).toEqual({
      total: 3,
      domainEvents: 1,
      telemetryEntries: 2,
      failures: 1,
      components: ["executor", "model"],
    });
  });

  it("normalizes display metadata without exposing payloads or adapter fields", () => {
    expect(timelineName(items[0]!)).toBe("task.created");
    expect(timelineTimestamp(items[1]!)).toBe("2026-09-03T02:00:01+00:00");
    expect(timelineContext(items[2]!)).toBe("model");
  });
});
