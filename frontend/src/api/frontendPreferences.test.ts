import { describe, expect, it, vi } from "vitest";
import { ApiTransport } from "./transport";
import { FrontendPreferencesClient } from "./frontendPreferences";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("FrontendPreferencesClient", () => {
  it("reads and mutates only through the canonical collection/command boundary", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const preference = {
      id: "user:test",
      type: "frontend-preference",
      scope: "user",
      schema_version: 1,
      revision: 1,
      updated_at: "2026-09-26T15:00:00+00:00",
      customization: { version: 1, appearance: { accent: "#112233" } },
    };
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init: init ?? {} });
      if (calls.length === 1) {
        return jsonResponse({
          items: [preference],
          next_cursor: null,
          total: 1,
          limit: 1,
        });
      }
      if (calls.length === 2) {
        return jsonResponse({ ...preference, revision: 2 });
      }
      return jsonResponse({
        ...preference,
        revision: 0,
        updated_at: null,
        customization: null,
      });
    });
    const transport = new ApiTransport({ fetchImpl });
    const client = new FrontendPreferencesClient({ transport });

    await expect(client.preference()).resolves.toMatchObject({ id: "user:test", revision: 1 });
    await expect(
      client.update(
        "user:test",
        { version: 1, appearance: { accent: "#445566" } },
        1,
      ),
    ).resolves.toMatchObject({ revision: 2 });
    await expect(client.reset("user:test", 2)).resolves.toMatchObject({
      revision: 0,
      customization: null,
    });

    expect(calls[0]?.url).toContain("/api/v1/frontend-preferences?limit=1");
    expect(calls[1]?.url).toContain("/api/v1/commands/frontend-preference.update");
    expect(calls[2]?.url).toContain("/api/v1/commands/frontend-preference.reset");

    const updateHeaders = new Headers(calls[1]?.init.headers);
    const resetHeaders = new Headers(calls[2]?.init.headers);
    expect(updateHeaders.get("idempotency-key")).toBeTruthy();
    expect(resetHeaders.get("idempotency-key")).toBeTruthy();

    expect(JSON.parse(String(calls[1]?.init.body))).toEqual({
      resource_ref: "user:test",
      schema_version: 1,
      customization: { version: 1, appearance: { accent: "#445566" } },
      expected_revision: 1,
    });
    expect(JSON.parse(String(calls[2]?.init.body))).toEqual({
      resource_ref: "user:test",
      expected_revision: 2,
    });
  });
});
