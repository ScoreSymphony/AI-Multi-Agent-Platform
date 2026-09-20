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

  it("delegates device secrets to expo-secure-store rather than ordinary app storage", () => {
    const secureStore = readFileSync(resolve(ROOT, "src/secureStore.ts"), "utf8");
    const session = readFileSync(resolve(ROOT, "src/session.ts"), "utf8");
    expect(secureStore).toContain('from "expo-secure-store"');
    expect(secureStore).toContain("SecureStore.getItemAsync");
    expect(secureStore).toContain("SecureStore.setItemAsync");
    expect(secureStore).toContain("SecureStore.deleteItemAsync");
    expect(session).not.toContain("AsyncStorage");
    expect(session).not.toContain("localStorage");
  });

  it("uses short-lived pairing instead of manual durable-token entry", () => {
    const app = readFileSync(resolve(ROOT, "App.tsx"), "utf8");
    const session = readFileSync(resolve(ROOT, "src/session.ts"), "utf8");
    expect(app).toContain("Confirm server & pair");
    expect(app).toContain("Pairing code");
    expect(app).not.toContain("Bearer credential</Text>");
    expect(session).toContain("/api/v1/auth/mobile-pairings:consume");
    expect(session).toContain("/api/v1/auth/me");
  });

  it("keeps stale offline state visibly read-only in the application surface", () => {
    const app = readFileSync(resolve(ROOT, "App.tsx"), "utf8");
    expect(app).toContain("Showing cached read-only data.");
    expect(app).toContain("Mutations are disabled");
  });

  it("does not import server runtime packages into the mobile application", () => {
    const app = readFileSync(resolve(ROOT, "App.tsx"), "utf8");
    expect(app).not.toContain("ai_multi_agent_platform");
    expect(app).not.toContain("../src/ai_multi_agent_platform");
  });
});
