import { describe, expect, it } from "vitest";
import {
  DEFAULT_CRITERIA,
  parseCriteria,
  parseEvidence,
  requireText,
} from "./goalInput";

describe("Goal input parsing", () => {
  it("parses the default success criterion independently of route rendering", () => {
    expect(parseCriteria(DEFAULT_CRITERIA)).toEqual([
      expect.objectContaining({ criterion_id: "acceptance", required: true }),
    ]);
  });

  it("rejects empty or malformed criteria collections", () => {
    expect(() => parseCriteria("[]")).toThrow("success criteria must be a non-empty JSON array");
    expect(() => parseCriteria("not-json")).toThrow("success criteria must contain valid JSON");
  });

  it("requires evidence to remain an explicit array", () => {
    expect(parseEvidence("[]")).toEqual([]);
    expect(() => parseEvidence("{}")).toThrow("evidence must be a JSON array");
  });

  it("normalizes required text at the interaction boundary", () => {
    expect(requireText("  review  ", "title")).toBe("review");
    expect(() => requireText("   ", "title")).toThrow("title must not be blank");
  });
});
