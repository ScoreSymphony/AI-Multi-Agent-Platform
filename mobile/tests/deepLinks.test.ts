import { describe, expect, it } from "vitest";

import { mobileRouteHref, parseMobileDeepLink } from "../src/deepLinks";

describe("mobile deep links", () => {
  it("accepts only canonical allowlisted resource routes", () => {
    expect(parseMobileDeepLink("aiagentplatform://task/task-123")).toEqual({
      kind: "task",
      id: "task-123",
    });
    expect(mobileRouteHref({ kind: "approval", id: "approval:42" })).toBe(
      "aiagentplatform://approval/approval%3A42",
    );
  });

  it("rejects arbitrary schemes, query actions and path injection", () => {
    expect(parseMobileDeepLink("https://evil.example/task/1")).toBeNull();
    expect(parseMobileDeepLink("aiagentplatform://task/a/b")).toBeNull();
    expect(parseMobileDeepLink("aiagentplatform://task/task-1?action=approve")).toBeNull();
    expect(parseMobileDeepLink("aiagentplatform://unknown/id")).toBeNull();\n    expect(parseMobileDeepLink("aiagentplatform://task/%E0%A4%A")).toBeNull();
  });
});
