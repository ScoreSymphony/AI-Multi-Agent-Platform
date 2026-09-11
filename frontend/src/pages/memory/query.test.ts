import { describe, expect, it } from "vitest";
import { buildMemoryQueryKey, memoryTypeFilterValue } from "./query";

describe("memory query presentation state", () => {
  it("normalizes presentation-only text fields when building the reload key", () => {
    expect(
      buildMemoryQueryKey({
        scope: "workspace",
        scopeId: "  workspace-1  ",
        projectId: "  project-1  ",
        search: "  cadence  ",
        memoryType: "semantic",
        includeExpired: false,
        includeSuperseded: true,
      }),
    ).toBe("workspace|workspace-1|project-1|cadence|semantic|false|true");
  });

  it("maps the all filter to no canonical memory-type restriction", () => {
    expect(memoryTypeFilterValue("all")).toBeUndefined();
    expect(memoryTypeFilterValue("procedural")).toBe("procedural");
  });
});
