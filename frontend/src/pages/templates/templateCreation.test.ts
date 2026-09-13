import { describe, expect, it } from "vitest";
import { optionalRevision, parseTemplateContent } from "./templateCreation";

describe("Template creation input", () => {
  it("parses canonical Template JSON separately from route rendering", () => {
    expect(parseTemplateContent('{"name":"Research","template_type":"agent"}')).toMatchObject({
      name: "Research",
      template_type: "agent",
    });
  });

  it("rejects non-canonical JSON shapes", () => {
    expect(() => parseTemplateContent("[]")).toThrow("Template content must be a canonical JSON object");
    expect(() => parseTemplateContent('{"name":"Research"}')).toThrow("Template content must be a canonical JSON object");
  });

  it("normalizes optional positive revisions", () => {
    expect(optionalRevision(" ")).toBeUndefined();
    expect(optionalRevision(" 3 ")).toBe(3);
    expect(() => optionalRevision("0")).toThrow("Project Template revision must be a positive integer");
  });
});
