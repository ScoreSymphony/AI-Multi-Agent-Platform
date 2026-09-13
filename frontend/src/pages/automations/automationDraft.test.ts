import { describe, expect, it } from "vitest";
import {
  draftFromAutomation,
  parseJsonObject,
  toCreateInput,
  validateAutomationDraft,
} from "./automationDraft";

describe("automation draft mapping", () => {
  it("validates and maps a canonical manual automation draft", () => {
    const draft = {
      ...draftFromAutomation(),
      name: "  Daily review  ",
      description: "  Review queued work  ",
      taskTitle: "  Review queue  ",
      taskObjective: "  Inspect pending work  ",
      taskPayloadJson: "{\"priority\":\"normal\"}",
    };

    expect(() => validateAutomationDraft(draft)).not.toThrow();
    expect(toCreateInput(draft)).toMatchObject({
      name: "Daily review",
      description: "Review queued work",
      trigger: { type: "manual", timezone: "UTC", filters: {} },
      task_template: {
        title: "Review queue",
        objective: "Inspect pending work",
        payload: { priority: "normal" },
      },
      retry_policy: { max_attempts: 3, base_backoff_seconds: 1 },
    });
  });

  it("rejects invalid recurring intervals before transport mapping", () => {
    const draft = {
      ...draftFromAutomation(),
      name: "Recurring review",
      taskTitle: "Review queue",
      taskObjective: "Inspect pending work",
      triggerType: "recurring" as const,
      at: "2026-09-14T08:00:00+02:00",
      intervalSeconds: "0",
    };

    expect(() => validateAutomationDraft(draft)).toThrow("recurring interval must be positive");
  });

  it("accepts only JSON objects for payload-bearing fields", () => {
    expect(parseJsonObject("{\"ok\":true}", "payload")).toEqual({ ok: true });
    expect(() => parseJsonObject("[]", "payload")).toThrow("payload must be a JSON object");
    expect(() => parseJsonObject("not-json", "payload")).toThrow("payload must be valid JSON");
  });
});
