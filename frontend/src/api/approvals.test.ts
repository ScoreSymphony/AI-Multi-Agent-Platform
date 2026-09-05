import { describe, expect, it, vi } from "vitest";
import { ApprovalClient, type CanonicalApproval } from "./approvals";
import { BrowserSessionClient } from "./browserSession";

const pendingApproval: CanonicalApproval = {
  id: "approval_123e4567-e89b-42d3-a456-426614174000",
  type: "approval",
  status: "pending",
  subject_type: "tool_invocation",
  subject_id: "tool-call-1",
  owner_ref: { type: "user", id: "user_alice" },
  requester_ref: "agent_runner",
  action: "invoke_sensitive_capability",
  resource_type: "capability",
  resource_id: "capability_shell",
  requested_action_digest: "sha256:exact-requested-action",
  risk: "high",
  policy_id: "policy-sensitive-tools",
  reason: "Sensitive capability requires human approval",
  project_id: "project_1",
  task_id: "task_1",
  run_id: "run_1",
  capability_ref: "capability_shell",
  payload_ref: "payload_ref_redacted",
  created_at: "2026-09-05T20:00:00Z",
  expires_at: "2026-09-05T21:00:00Z",
  decision_by: null,
  decision_at: null,
  decision_comment: null,
};

const rejectedDecisions: Array<[number, string, string]> = [
  [403, "forbidden", "actor may not approve"],
  [409, "conflict", "requested action digest changed"],
  [409, "conflict", "approval is expired or not pending"],
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "X-Request-ID": "request-test",
      "X-Correlation-ID": "correlation-test",
    },
  });
}

function canonicalError(status: number, code: string, message: string): Response {
  return jsonResponse(
    {
      code,
      category: status === 403 ? "authorization" : "conflict",
      message,
      request_id: "request-test",
      correlation_id: "correlation-test",
      retryable: false,
    },
    status,
  );
}

describe("ApprovalClient", () => {
  it("keeps Approval inspection on the canonical read-only collection", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      if (String(input).includes("/approvals/")) return jsonResponse(pendingApproval);
      return jsonResponse({ items: [pendingApproval], next_cursor: null, total: 1, limit: 50 });
    });
    const client = new ApprovalClient({ fetchImpl });

    const page = await client.listApprovals({ filters: { status: "pending" }, limit: 50 });
    const detail = await client.getApproval(pendingApproval.id);

    expect(page.items[0]?.id).toBe(pendingApproval.id);
    expect(detail.requested_action_digest).toBe(pendingApproval.requested_action_digest);
    expect(calls).toEqual([
      "/api/v1/approvals?limit=50&filter%5Bstatus%5D=pending",
      `/api/v1/approvals/${encodeURIComponent(pendingApproval.id)}`,
    ]);
  });

  it("sends approve and deny only through the shared canonical decision commands", async () => {
    const calls: Array<{ url: string; headers: Headers; body: Record<string, unknown> }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        headers: new Headers(init?.headers),
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      });
      return jsonResponse({ ...pendingApproval, status: calls.length === 1 ? "approved" : "denied" });
    });
    const client = new ApprovalClient({ fetchImpl });

    const approved = await client.approve(
      pendingApproval.id,
      pendingApproval.requested_action_digest,
      {
        comment: "Reviewed exact action",
        idempotencyKey: "approve-key",
        correlationId: "approve-correlation",
      },
    );
    const denied = await client.deny(
      pendingApproval.id,
      pendingApproval.requested_action_digest,
      {
        idempotencyKey: "deny-key",
        correlationId: "deny-correlation",
      },
    );

    expect(approved.status).toBe("approved");
    expect(denied.status).toBe("denied");
    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/commands/approval.approve",
      "/api/v1/commands/approval.deny",
    ]);
    expect(calls[0]?.headers.get("Idempotency-Key")).toBe("approve-key");
    expect(calls[0]?.headers.get("X-Correlation-ID")).toBe("approve-correlation");
    expect(calls[0]?.body).toEqual({
      resource_ref: pendingApproval.id,
      requested_action_digest: pendingApproval.requested_action_digest,
      comment: "Reviewed exact action",
    });
    expect(calls[1]?.body).toEqual({
      resource_ref: pendingApproval.id,
      requested_action_digest: pendingApproval.requested_action_digest,
    });
    expect(calls[0]?.body).not.toHaveProperty("payload");
    expect(calls[0]?.body).not.toHaveProperty("proposed_payload");
    expect(calls[0]?.body).not.toHaveProperty("secret");
    expect(calls[0]?.url).not.toContain("ApprovalService");
  });

  it("uses the BrowserSession CSRF boundary for cookie-authenticated decisions", async () => {
    const csrfStorage = new Map<string, string>([
      ["ai-agent-platform.csrf-token", "csrf-approval-test"],
    ]);
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/approval.approve");
      const headers = new Headers(init?.headers);
      expect(headers.get("X-CSRF-Token")).toBe("csrf-approval-test");
      expect(headers.get("Idempotency-Key")).toBe("browser-decision-key");
      expect(headers.get("X-Correlation-ID")).toBe("browser-correlation");
      expect(init?.credentials).toBe("include");
      return jsonResponse({ ...pendingApproval, status: "approved" });
    });
    const session = new BrowserSessionClient({
      fetchImpl,
      storage: {
        getItem: (key) => csrfStorage.get(key) ?? null,
        setItem: (key, value) => void csrfStorage.set(key, value),
        removeItem: (key) => void csrfStorage.delete(key),
      },
    });
    const client = new ApprovalClient({ fetchImpl: session.fetch });

    await client.approve(
      pendingApproval.id,
      pendingApproval.requested_action_digest,
      { idempotencyKey: "browser-decision-key", correlationId: "browser-correlation" },
    );

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it.each(rejectedDecisions)(
    "propagates canonical decision rejection HTTP %i without a fallback",
    async (status, code, message) => {
      const fetchImpl = vi.fn(async () => canonicalError(status, code, message));
      const client = new ApprovalClient({ fetchImpl });

      await expect(
        client.approve(
          pendingApproval.id,
          pendingApproval.requested_action_digest,
          { idempotencyKey: `rejection-${status}`, correlationId: `correlation-${status}` },
        ),
      ).rejects.toMatchObject({
        status,
        body: expect.objectContaining({ code, message }),
      });
      expect(fetchImpl).toHaveBeenCalledOnce();
    },
  );

  it("rejects invalid client input before any mutation request", async () => {
    const fetchImpl = vi.fn();
    const client = new ApprovalClient({ fetchImpl });

    await expect(client.approve("", "digest")).rejects.toThrow("Approval ID is required");
    await expect(client.approve(pendingApproval.id, " ")).rejects.toThrow(
      "Requested action digest is required",
    );
    await expect(
      client.deny(pendingApproval.id, pendingApproval.requested_action_digest, { comment: " " }),
    ).rejects.toThrow("Approval decision comment is required");
    await expect(
      client.approve(pendingApproval.id, pendingApproval.requested_action_digest, {
        idempotencyKey: " ",
      }),
    ).rejects.toThrow("Approval decision idempotency key is required");
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
