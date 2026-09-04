import { describe, expect, it, vi } from "vitest";
import { VerificationClient } from "./verification";

const verification = {
  id: "verification_test",
  type: "verification",
  task_id: "task_test",
  run_id: "run_test",
  result_id: "result_test",
  artifact_ids: [],
  project_id: null,
  capability_ids: [],
  policy: { id: "verification_policy_test", version: 1 },
  stage_id: "human-review",
  subject: { type: "result", id: "result_test", revision: "1", digest: "sha256:test" },
  requested_verifier_kind: "human",
  requested_capability_ref: null,
  repair_attempt: 0,
  status: "pending",
  created_at: "2026-09-04T00:00:00+00:00",
  expires_at: null,
  correlation_id: "corr-test",
  causation_id: null,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("#86 canonical Verification frontend client", () => {
  it("reads history, review queue and completion requirements from extension collections", async () => {
    const fetchSpy = vi.fn().mockImplementation(() =>
      Promise.resolve(
        jsonResponse({ items: [verification], next_cursor: null, total: 1, limit: 50 }),
      ),
    );
    const client = new VerificationClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.list({ filters: { task_id: "task_test" } });
    await client.listPendingReviews();
    await client.listRequirements();

    const urls = fetchSpy.mock.calls.map(([url]) => new URL(url as string, "https://platform.invalid"));
    expect(urls[0].pathname).toBe("/api/v1/verifications");
    expect(urls[0].searchParams.get("filter[task_id]")).toBe("task_test");
    expect(urls[1].pathname).toBe("/api/v1/verification-reviews");
    expect(urls[2].pathname).toBe("/api/v1/verification-requirements");
    for (const [, init] of fetchSpy.mock.calls as Array<[string, RequestInit]>) {
      expect(init.method).toBe("GET");
      expect(init.credentials).toBe("include");
    }
  });

  it("posts an exact authorized human-review command with retry-stable idempotency", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(verification));
    const client = new VerificationClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.requestChanges(
      "verification_test",
      {
        comment: "Please revise the output.",
        evidence_artifact_ids: ["artifact_evidence"],
      },
      "review-attempt-1",
    );

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(new URL(url, "https://platform.invalid").pathname).toBe(
      "/api/v1/commands/verification.request-changes",
    );
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    const headers = new Headers(init.headers);
    expect(headers.get("Idempotency-Key")).toBe("review-attempt-1");
    expect(headers.get("X-Correlation-ID")).toBeTruthy();
    expect(JSON.parse(init.body as string)).toEqual({
      resource_ref: "verification_test",
      comment: "Please revise the output.",
      evidence_artifact_ids: ["artifact_evidence"],
    });
  });

  it("does not send blank comments or empty evidence arrays", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(verification));
    const client = new VerificationClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.accept(
      "verification_test",
      { comment: "   ", evidence_artifact_ids: [] },
      "review-attempt-2",
    );

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(new URL(url, "https://platform.invalid").pathname).toBe(
      "/api/v1/commands/verification.accept",
    );
    expect(JSON.parse(init.body as string)).toEqual({ resource_ref: "verification_test" });
  });
});
