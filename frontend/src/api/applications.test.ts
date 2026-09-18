import { describe, expect, it, vi } from "vitest";
import { ApplicationsClient } from "./applications";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ApplicationsClient", () => {
  it("reads definitions, instances and diagnostics through canonical collections", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      if (String(input).includes("application-logs")) {
        return jsonResponse({
          id: "application_instance_test",
          type: "application-log-stream",
          entries: [],
        });
      }
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 50 });
    });
    const client = new ApplicationsClient({ fetchImpl });

    await client.listApplications({ limit: 20 });
    await client.listInstances({ limit: 30, sort: "updated_at", direction: "desc" });
    await client.getLogs("application_instance_test");

    expect(calls).toEqual([
      "/api/v1/applications?limit=20",
      "/api/v1/application-instances?limit=30&sort=updated_at&direction=desc",
      "/api/v1/application-logs/application_instance_test",
    ]);
  });

  it.each([
    ["start", "application.start"],
    ["stop", "application.stop"],
    ["restart", "application.restart"],
    ["reconcile", "application.reconcile"],
    ["remove", "application.remove"],
  ] as const)("forwards %s through the exact canonical command", async (method, command) => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`/api/v1/commands/${command}`);
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.has("idempotency-key")).toBe(true);
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "application_instance_test",
      });
      return jsonResponse({
        id: "application_instance_test",
        type: "application-instance",
      });
    });
    const client = new ApplicationsClient({ fetchImpl });

    await client[method]("application_instance_test");
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("sends mutable configuration through the canonical configure command", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/application.configure");
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "application_instance_test",
        configuration: { label: "changed", workers: 4 },
      });
      return jsonResponse({
        id: "application_instance_test",
        type: "application-instance",
      });
    });
    const client = new ApplicationsClient({ fetchImpl });

    await client.configure("application_instance_test", {
      label: "changed",
      workers: 4,
    });

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("rejects blank resource references before transport", () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL) => jsonResponse({}));
    const client = new ApplicationsClient({ fetchImpl });

    expect(() => client.getInstance(" ")).toThrow("application reference is required");
    expect(() => client.getLogs(" ")).toThrow("application reference is required");
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
