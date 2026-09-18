import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = resolve(import.meta.dirname, "..");

describe("mobile architecture boundary", () => {
  it("keeps mobile HTTP traffic on the canonical Control Plane", () => {
    const client = readFileSync(resolve(ROOT, "src/client.ts"), "utf8");
    expect(client).toContain("/api/v1");
    for (const forbidden of [
      "litellm",
      "forge",
      "hermes",
      "mcp://",
      "postgres://",
      "PluginRegistry",
    ]) {
      expect(client.toLowerCase()).not.toContain(forbidden.toLowerCase());
    }
  });

  it("does not import server runtime packages into the mobile application", () => {
    const app = readFileSync(resolve(ROOT, "App.tsx"), "utf8");
    expect(app).not.toContain("ai_multi_agent_platform");
    expect(app).not.toContain("../src/ai_multi_agent_platform");
  });
});
