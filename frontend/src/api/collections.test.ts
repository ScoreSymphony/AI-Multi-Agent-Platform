import { describe, expect, it, vi } from "vitest";
import { ControlPlaneCollectionClient } from "./collections";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ControlPlaneCollectionClient", () => {
  it("forwards opaque cursors and server-side filters without decoding them", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(
        "/api/v1/approvals?limit=25&cursor=opaque%3Apage%3A2&sort=id&direction=asc&filter%5Bstatus%5D=pending",
      );
      expect(init?.method).toBe("GET");
      expect(init?.credentials).toBe("include");
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 25 });
    });
    const client = new ControlPlaneCollectionClient({ fetchImpl });

    await client.list("approvals", {
      limit: 25,
      cursor: "opaque:page:2",
      sort: "id",
      direction: "asc",
      filters: { status: "pending" },
    });

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("encodes canonical resource IDs on detail reads", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v1/approvals/approval%2Fspecial");
      return jsonResponse({ id: "approval/special" });
    });
    const client = new ControlPlaneCollectionClient({ fetchImpl });

    await client.get("approvals", "approval/special");
  });

  it("rejects collection path injection before issuing a request", async () => {
    const fetchImpl = vi.fn();
    const client = new ControlPlaneCollectionClient({ fetchImpl });

    await expect(client.list("../private-backend")).rejects.toThrow(
      "invalid canonical collection",
    );
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
