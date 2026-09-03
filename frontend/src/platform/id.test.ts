import { describe, expect, it } from "vitest";
import { compactCanonicalId, isCanonicalId } from "./id";

describe("canonical ID helpers", () => {
  it("accepts canonical UUID-backed platform IDs", () => {
    expect(isCanonicalId("task_123e4567-e89b-42d3-a456-426614174000")).toBe(true);
    expect(isCanonicalId("run_123e4567-e89b-42d3-a456-426614174000")).toBe(true);
  });

  it("does not treat provider or derived telemetry identifiers as canonical domain IDs", () => {
    expect(isCanonicalId("forge-task-42")).toBe(false);
    expect(isCanonicalId("telemetry_deadbeef")).toBe(false);
    expect(compactCanonicalId("telemetry_deadbeef")).toBe("telemetry_deadbeef");
  });
});
