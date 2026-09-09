import { describe, expect, it } from "vitest";
import type { APImanifest } from "../api/types";
import { learningManifestCapabilities } from "./learningManifest";

function manifest(
  resources: string[],
  commands: string[] = [],
): APImanifest {
  return {
    api_version: "v1",
    resources,
    commands,
    openapi: "/api/v1/openapi.json",
    live_updates: "/api/v1/events",
  };
}

describe("#595 governed Learning manifest integration", () => {
  it("stays unavailable until both canonical Learning collections are advertised", () => {
    expect(
      learningManifestCapabilities(
        "ready",
        manifest(["learning-candidates"]),
      ).state,
    ).toBe("unavailable");
  });

  it("supports read-only inspection without inventing mutation commands", () => {
    const capabilities = learningManifestCapabilities(
      "ready",
      manifest(["learning-candidates", "learning-feedback"]),
    );

    expect(capabilities.state).toBe("read_only");
    expect(capabilities.commands).toEqual([]);
    expect(capabilities.postPromotionAvailable).toBe(false);
  });

  it("passes through only advertised Learning decisions and optional post-promotion evidence", () => {
    const capabilities = learningManifestCapabilities(
      "ready",
      manifest(
        [
          "learning-candidates",
          "learning-feedback",
          "learning-post-promotion-evaluations",
        ],
        [
          "learning.accept",
          "learning.promote",
          "registry.activate",
          "unrelated.command",
        ],
      ),
    );

    expect(capabilities.state).toBe("available");
    expect(capabilities.commands).toEqual([
      "learning.accept",
      "learning.promote",
    ]);
    expect(capabilities.postPromotionAvailable).toBe(true);
  });
});
