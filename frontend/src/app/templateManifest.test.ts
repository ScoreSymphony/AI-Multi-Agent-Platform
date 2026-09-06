import { describe, expect, it } from "vitest";
import type { APImanifest } from "../api/types";
import {
  TEMPLATE_REQUIRED_COMMANDS,
  TEMPLATE_REQUIRED_RESOURCES,
  templateManifestState,
} from "./templateManifest";

function manifest(overrides: Partial<APImanifest> = {}): APImanifest {
  return {
    api_version: "v1",
    resources: [...TEMPLATE_REQUIRED_RESOURCES],
    commands: [...TEMPLATE_REQUIRED_COMMANDS],
    openapi: "/api/v1/openapi.json",
    live_updates: "sse",
    ...overrides,
  };
}

describe("Template manifest gating", () => {
  it("remains unavailable until discovery is ready", () => {
    expect(templateManifestState("loading", null)).toBe("loading");
    expect(templateManifestState("unavailable", null)).toBe("unavailable");
  });

  it("requires every canonical resource used by the final Template surface", () => {
    for (const resource of TEMPLATE_REQUIRED_RESOURCES) {
      const resources = TEMPLATE_REQUIRED_RESOURCES.filter((item) => item !== resource);
      expect(templateManifestState("ready", manifest({ resources: [...resources] }))).toBe(
        "unavailable",
      );
    }
  });

  it("keeps instantiated owner-domain detail collections in the product contract", () => {
    for (const resource of [
      "workflows",
      "capability-assignments",
      "model-routing-profiles",
    ]) {
      expect(TEMPLATE_REQUIRED_RESOURCES).toContain(resource);
    }
  });

  it("fails closed when commands are absent or any Template command is missing", () => {
    expect(templateManifestState("ready", manifest({ commands: undefined }))).toBe(
      "unavailable",
    );
    for (const command of TEMPLATE_REQUIRED_COMMANDS) {
      const commands = TEMPLATE_REQUIRED_COMMANDS.filter((item) => item !== command);
      expect(templateManifestState("ready", manifest({ commands: [...commands] }))).toBe(
        "unavailable",
      );
    }
  });

  it("keeps the post-owner-domain create-from-existing exporters in the product contract", () => {
    for (const command of [
      "template.create-from-workflow",
      "template.create-from-capability-assignment",
      "template.create-from-model-routing-profile",
    ]) {
      expect(TEMPLATE_REQUIRED_COMMANDS).toContain(command);
    }
  });

  it("mounts only when the complete Template product contract is advertised", () => {
    expect(templateManifestState("ready", manifest())).toBe("available");
  });
});
