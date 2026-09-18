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

  it("maps a human authenticated actor to the canonical user Task owner", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "task-1", title: "Task", objective: "Do work", status: "created" }), { status: 201 }),
    );
    const client = new MobileControlPlaneClient({
      baseUrl: "https://platform.example",
      credentialSource,
      fetchImpl,
    });

    await client.createTask(
      {
        actor_id: "user-1",
        actor_type: "human",
        authentication_method: "personal_access",
        credential_id: "cred-1",
        authenticated_at: "2026-09-19T00:00:00Z",
        expires_at: null,
        organization_id: null,
        project_id: null,
      },
      "Task",
      "Do work",
    );

    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://platform.example/api/v1/tasks");
    expect(JSON.parse(String(init.body))).toMatchObject({
      owner_type: "user",
      owner_id: "user-1",
      title: "Task",
      objective: "Do work",
    });
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
          actor_type: "human",
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

  it("does not disguise invalid server JSON as an offline stale read", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [], next_cursor: null, total: 0, limit: 50 }), { status: 200 }),
      )
      .mockResolvedValueOnce(new Response("not-json", { status: 200 }));
    const client = new MobileControlPlaneClient({
      baseUrl: "https://platform.example",
      credentialSource,
      fetchImpl,
    });

    await client.listTasks();
    await expect(client.listTasks()).rejects.toThrow("invalid JSON");
    expect(client.isOffline()).toBe(false);
  });

  it("clears the active secure session hook when the server revokes a credential", async () => {
    const onUnauthorized = vi.fn();
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: "unauthorized", message: "revoked" }), { status: 401 }),
    );
    const client = new MobileControlPlaneClient({
      baseUrl: "https://platform.example",
      credentialSource,
      fetchImpl,
      onUnauthorized,
    });

    await expect(client.me()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledOnce();
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

  it("resumes a waiting Task only through the canonical Conversation input route", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "task-1", status: "queued" }), { status: 200 }),
    );
    const client = new MobileControlPlaneClient({
      baseUrl: "https://platform.example",
      credentialSource,
      fetchImpl,
    });

    await client.resumeWaitingTask("message-1", "task-1");

    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "https://platform.example/api/v1/conversation-messages/message-1:resume-task",
    );
    expect(JSON.parse(String(init.body))).toEqual({ task_id: "task-1" });
    expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
  });

  it("uses only canonical Verification review commands", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "verification-1", status: "completed" }), { status: 200 }),
    );
    const client = new MobileControlPlaneClient({
      baseUrl: "https://platform.example",
      credentialSource,
      fetchImpl,
    });

    await client.verificationReview("verification-1", "verification.request-changes", "fix it");

    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "https://platform.example/api/v1/commands/verification.request-changes",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      resource_ref: "verification-1",
      comment: "fix it",
    });
  });

  it("updates canonical Notification state without creating local authority", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "notification-1", state: "read" }), { status: 200 }),
    );
    const client = new MobileControlPlaneClient({
      baseUrl: "https://platform.example",
      credentialSource,
      fetchImpl,
    });

    await client.markNotificationRead("notification-1");

    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "https://platform.example/api/v1/commands/notification.mark-read",
    );
    expect(JSON.parse(String(init.body))).toEqual({ resource_ref: "notification-1" });
  });
});
