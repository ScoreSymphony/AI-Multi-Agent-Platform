import { describe, expect, it, vi } from "vitest";
import { ComputeClient } from "./compute";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ComputeClient", () => {
  it("reads nodes, workers and worker jobs only through canonical Control Plane collections", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 50 });
    });
    const client = new ComputeClient({ fetchImpl });

    await client.listNodes({ limit: 20, cursor: "opaque-node-cursor" });
    await client.listWorkers({ limit: 30, cursor: "opaque-worker-cursor" });
    await client.listWorkerJobs({ limit: 40, cursor: "opaque-job-cursor" });

    expect(calls).toEqual([
      "/api/v1/nodes?limit=20&cursor=opaque-node-cursor",
      "/api/v1/workers?limit=30&cursor=opaque-worker-cursor",
      "/api/v1/worker-jobs?limit=40&cursor=opaque-job-cursor",
    ]);
  });

  it("uses canonical IDs for node, worker and worker-job detail reads", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return jsonResponse({ id: "resource" });
    });
    const client = new ComputeClient({ fetchImpl });

    await client.getNode("node:alpha/1");
    await client.getWorker("worker:alpha/1");
    await client.getWorkerJob("worker-job:alpha/1");

    expect(calls).toEqual([
      "/api/v1/nodes/node%3Aalpha%2F1",
      "/api/v1/workers/worker%3Aalpha%2F1",
      "/api/v1/worker-jobs/worker-job%3Aalpha%2F1",
    ]);
  });

  it.each([
    ["drainNode", "node.drain"],
    ["undrainNode", "node.undrain"],
    ["enableNodeMaintenance", "node.maintenance-enable"],
    ["disableNodeMaintenance", "node.maintenance-disable"],
  ] as const)("forwards %s through the exact canonical command", async (method, command) => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`/api/v1/commands/${command}`);
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.get("idempotency-key")).toBe("compute-key");
      expect(headers.has("x-correlation-id")).toBe(true);
      expect(JSON.parse(String(init?.body))).toEqual({ resource_ref: "node-1" });
      return jsonResponse({ id: "node-1" });
    });
    const client = new ComputeClient({ fetchImpl });

    await client[method]("node-1", "compute-key");
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it.each([
    ["drainWorker", "worker.drain"],
    ["undrainWorker", "worker.undrain"],
  ] as const)("forwards %s through the exact canonical command", async (method, command) => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`/api/v1/commands/${command}`);
      expect(JSON.parse(String(init?.body))).toEqual({ resource_ref: "worker-1" });
      return jsonResponse({ id: "worker-1" });
    });
    const client = new ComputeClient({ fetchImpl });

    await client[method]("worker-1", "compute-key");
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("rejects blank canonical references and idempotency keys before transport", async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({}));
    const client = new ComputeClient({ fetchImpl });

    expect(() => client.getNode(" ")).toThrow("node reference is required");
    expect(() => client.drainWorker(" ")).toThrow("worker reference is required");
    await expect(client.drainNode("node-1", " ")).rejects.toThrow(
      "compute idempotency key is required",
    );
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
