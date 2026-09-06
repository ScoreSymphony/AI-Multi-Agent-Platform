import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient } from "./browserSession";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("BrowserSessionClient release status", () => {
  it("queries the authenticated read-only release status route", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/release/status");
      expect(init?.method).toBe("GET");
      expect(init?.credentials).toBe("include");
      return jsonResponse({
        platform_release: "0.0.1",
        versions: {},
        release_manifest: null,
        compatibility_inventory: {
          schema_version: "1",
          platform_release: "0.0.1",
          generated_from: "upstream/*.yaml",
          last_reviewed_at: "2026-09-03T00:00:00Z",
          components: [],
        },
        update_discovery: {
          mode: "disabled",
          observed_at: null,
          update_available: false,
          candidates: [],
        },
        release_ready: null,
        operator_warnings: [],
        automatic_production_updates: false,
        production_pin_mutation: "not_permitted_by_discovery",
      });
    });
    const session = new BrowserSessionClient({ fetchImpl, storage: null });

    const status = await session.releaseStatus();

    expect(status.platform_release).toBe("0.0.1");
    expect(status.update_discovery.mode).toBe("disabled");
    expect(status.automatic_production_updates).toBe(false);
  });
});
