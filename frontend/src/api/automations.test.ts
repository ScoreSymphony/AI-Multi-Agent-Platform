import { describe, expect, it, vi } from "vitest";
import { AutomationClient } from "./automations";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AutomationClient", () => {
  it("creates automations through the canonical command route with idempotency", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/automation.create");
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.has("idempotency-key")).toBe(true);
      expect(headers.has("x-correlation-id")).toBe(true);
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body.resource_ref).toBe("automations");
      expect(body.name).toBe("Nightly checks");
      return jsonResponse({ id: "automation_1", type: "automation" });
    });
    const client = new AutomationClient({ fetchImpl });

    await client.create({
      name: "Nightly checks",
      trigger: {
        type: "recurring",
        timezone: "UTC",
        at: "2026-09-04T00:00:00+00:00",
        interval_seconds: 86400,
      },
      task_template: {
        title: "Run checks",
        objective: "Execute the configured checks",
      },
    });

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("uses exact automation lifecycle and manual-test commands", async () => {
    const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      });
      return jsonResponse({ id: "resource_1" });
    });
    const client = new AutomationClient({ fetchImpl });

    await client.pause("automation_1");
    await client.resume("automation_1");
    await client.disable("automation_1");
    await client.test("automation_1", { dry_run: true });
    await client.retryDelivery("delivery_1");

    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/commands/automation.pause",
      "/api/v1/commands/automation.resume",
      "/api/v1/commands/automation.disable",
      "/api/v1/commands/automation.test",
      "/api/v1/commands/automation.retry-delivery",
    ]);
    expect(calls[0]?.body.resource_ref).toBe("automation_1");
    expect(calls[3]?.body).toEqual({
      resource_ref: "automation_1",
      payload: { dry_run: true },
    });
    expect(calls[4]?.body.resource_ref).toBe("delivery_1");
  });

  it("updates only through the canonical automation.update command", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/automation.update");
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body).toMatchObject({
        resource_ref: "automation_1",
        name: "Updated automation",
        overlap_policy: "allow",
      });
      return jsonResponse({ id: "automation_1", type: "automation" });
    });
    const client = new AutomationClient({ fetchImpl });

    await client.update("automation_1", {
      name: "Updated automation",
      overlap_policy: "allow",
    });
  });
});
