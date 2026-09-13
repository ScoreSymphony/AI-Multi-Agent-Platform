import { describe, expect, it } from "vitest";
import {
  emptyAgentProfile,
  updateCapabilityBucket,
  validateAgent,
} from "./agentConfigurationState";

describe("Agent configuration interaction state", () => {
  it("keeps required capabilities in the allowlist", () => {
    const profile = { ...emptyAgentProfile(), name: "Reviewer", role: "reviewer" };
    const required = updateCapabilityBucket(profile, "required", ["capability:search"]);
    expect(required.capabilities.allowed).toContain("capability:search");
    expect(required.capabilities.constraints).toEqual([
      expect.objectContaining({ capability_id: "capability:search", required: true }),
    ]);
  });

  it("removes denied capabilities from allowlists and constraints", () => {
    let profile = { ...emptyAgentProfile(), name: "Reviewer", role: "reviewer" };
    profile = updateCapabilityBucket(profile, "required", ["capability:search"]);
    profile = updateCapabilityBucket(profile, "denied", ["capability:search"]);
    expect(profile.capabilities.allowed).not.toContain("capability:search");
    expect(profile.capabilities.constraints).toHaveLength(0);
  });

  it("retains existing validation semantics after extraction", () => {
    expect(validateAgent(emptyAgentProfile())).toBe("Agent name is required.");
  });
});
