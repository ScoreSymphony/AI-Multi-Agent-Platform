import { describe, expect, it, vi } from "vitest";
import { GovernanceClient } from "./governance";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("GovernanceClient", () => {
  it("requests clarification through the canonical registered command", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/proposal.request-clarification");
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.get("Idempotency-Key")).toBeTruthy();
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "proposal_test",
        expected_revision: 7,
      });
      return jsonResponse({
        id: "proposal_test",
        type: "proposal",
        status: "needs_spec",
        revision: 8,
      });
    });
    const client = new GovernanceClient({ fetchImpl });

    const proposal = await client.requestClarification("proposal_test", 7);

    expect(proposal.status).toBe("needs_spec");
    expect(proposal.revision).toBe(8);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("binds task conversion to the supplied canonical approval reference", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/specification.convert-to-task");
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "specification_test",
        approval_id: "approval_test",
      });
      return jsonResponse({
        id: "task_test",
        type: "task",
        status: "draft",
        project_id: null,
        governance: {},
      });
    });
    const client = new GovernanceClient({ fetchImpl });

    const task = await client.convertToTask("specification_test", "approval_test");

    expect(task.id).toBe("task_test");
    expect(fetchImpl).toHaveBeenCalledOnce();
  });
});
