import { describe, expect, it, vi } from "vitest";

import {
  MobileControlPlaneClient,
  OfflineMutationError,
  type CredentialSource,
} from "../src/client";
import type { CanonicalApproval } from "../src/types";

const credentialSource: CredentialSource = {
  async getToken() {
    return "mobile-token";
  },
};

describe("MobileControlPlaneClient", () => {
  it("uses only canonical /api/v1 reads with bearer authentication", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], next_cursor: null, total: 0, limit: 50 }), {
        status: 200,
      }),
    );
    const client = new MobileControlPlaneClient({
      baseUrl: "https://platform.example",
      credentialSource,
      fetchImpl,
    });

    const page = await client.listTasks();

    expect(page.stale).toBe(false);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "https://platform.example/api/v1/tasks?limit=50&sort=updated_at&direction=desc",
    );
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer mobile-token");
  });

  it("reuses last successful read as visibly stale data after a network failure", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [{ id: "run-1", task_id: "task-1", status: "running" }], next_cursor: null, total: 1, limit: 50 }), { status: 200 }),
      )
      .mockRejectedValueOnce(new Error("offline"));
    const client = new MobileControlPlaneClient({
      baseUrl: "https://platform.example",
      credentialSource,
      fetchImpl,
    });

    const fresh = await client.listRuns();
    const stale = await client.listRuns();

    expect(fresh.stale).toBe(false);
    expect(stale.stale).toBe(true);
    expect(stale.data.items[0]?.id).toBe("run-1");
    expect(client.isOffline()).toBe(true);
  });

  it("fails closed for mutations while offline and never queues them", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error("offline"));
    const client = new MobileControlPlaneClient({
      baseUrl: "https://platform.example",
      credentialSource,
      fetchImpl,
    });

    await expect(client.listTasks()).rejects.toThrow("offline");
    await expect(
      client.createTask(
        {
          actor_id: "user-1",
          actor_type: "user",
          authentication_method: "credential",
          credential_id: "cred-1",
          authenticated_at: "2026-09-19T00:00:00Z",
          expires_at: null,
          organization_id: null,
          project_id: null,
        },
        "Task",
        "Do work",
      ),
    ).rejects.toBeInstanceOf(OfflineMutationError);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("binds approval decisions to the exact canonical digest", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "approval-1", status: "approved" }), { status: 200 }),
    );
    const client = new MobileControlPlaneClient({
      baseUrl: "https://platform.example",
      credentialSource,
      fetchImpl,
    });
    const approval = {
      id: "approval-1",
      type: "approval",
      status: "pending",
      action: "task.start",
      resource_type: "task",
      resource_id: "task-1",
      requested_action_digest: "sha256:exact",
      risk: "high",
      reason: "review",
      created_at: "2026-09-19T00:00:00Z",
      expires_at: "2026-09-20T00:00:00Z",
    } satisfies CanonicalApproval;

    await client.approve(approval, "reviewed");

    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://platform.example/api/v1/commands/approval.approve");
    expect(JSON.parse(String(init.body))).toEqual({
      resource_ref: "approval-1",
      requested_action_digest: "sha256:exact",
      comment: "reviewed",
    });
    expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
  });
});
