import { describe, expect, it } from "vitest";
import type { APImanifest } from "../api/types";
import { approvalDecisionManifestState } from "./approvalManifest";

function manifest(
  resources: string[] = ["approvals"],
  commands: string[] | undefined = ["approval.approve", "approval.deny"],
): APImanifest {
  return {
    api_version: "v1",
    resources,
    commands,
    openapi: "/api/v1/openapi.json",
    live_updates: "sse",
  };
}

describe("Approval decision manifest gating", () => {
  it("does not expose decision controls while discovery is loading", () => {
    expect(approvalDecisionManifestState("loading", null)).toBe("loading");
  });

  it("keeps read-only Approval inspection when decision commands are absent", () => {
    expect(
      approvalDecisionManifestState("ready", { ...manifest(), commands: undefined }),
    ).toBe("unavailable");
    expect(
      approvalDecisionManifestState("ready", manifest(["approvals"], ["approval.approve"])),
    ).toBe("unavailable");
  });

  it("requires the canonical Approval resource and both safe decision commands", () => {
    expect(
      approvalDecisionManifestState(
        "ready",
        manifest([], ["approval.approve", "approval.deny"]),
      ),
    ).toBe("unavailable");
    expect(approvalDecisionManifestState("ready", manifest())).toBe("available");
  });

  it("fails closed when manifest discovery itself is unavailable", () => {
    expect(approvalDecisionManifestState("unavailable", null)).toBe("unavailable");
  });
});
