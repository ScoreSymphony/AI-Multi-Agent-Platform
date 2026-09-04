import { describe, expect, it } from "vitest";
import { terminalSessionFilters } from "./TerminalPage";

describe("terminalSessionFilters", () => {
  it("preserves project authorization scope with optional workspace and status filters", () => {
    expect(
      terminalSessionFilters(" project_123 ", " workspace_456 ", "running"),
    ).toEqual({
      project_id: "project_123",
      workspace_id: "workspace_456",
      status: "running",
    });
  });

  it("does not invent optional workspace or status filters", () => {
    expect(terminalSessionFilters("project_123", "", "")).toEqual({
      project_id: "project_123",
    });
  });
});
