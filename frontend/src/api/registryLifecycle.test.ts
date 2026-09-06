import { describe, expect, it, vi } from "vitest";
import { RegistryClient } from "./registry";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("RegistryClient lifecycle", () => {
  it("pins and unpins only through explicit idempotent Control Plane commands", async () => {
    const calls: Array<{ url: string; body: unknown; idempotency: string | null }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      calls.push({
        url: String(input),
        body: JSON.parse(String(init?.body)),
        idempotency: headers.get("Idempotency-Key"),
      });
      return jsonResponse({
        id: "example.asset",
        type: "registry-installation",
        item_id: "example.asset",
        version: "1.2.3",
        pinned_version: null,
        source_registry: "local",
        source_repository: "https://example.invalid/repo",
        package_reference: "asset@1.2.3",
        revision: null,
        license: "MIT",
        provenance: "release",
        history: [],
      });
    });
    const client = new RegistryClient({ fetchImpl });

    await client.pin("example.asset", "1.2.3", "pin-key");
    await client.unpin("example.asset", "unpin-key");

    expect(calls).toEqual([
      {
        url: "/api/v1/commands/registry.pin",
        body: { resource_ref: "example.asset", version: "1.2.3" },
        idempotency: "pin-key",
      },
      {
        url: "/api/v1/commands/registry.unpin",
        body: { resource_ref: "example.asset" },
        idempotency: "unpin-key",
      },
    ]);
  });
});
