import { describe, expect, it, vi } from "vitest";
import { DecisionRecordClient } from "./decisions";

const decision = {
  id: "decision_record_test",
  type: "decision-record",
  title: "Choose runtime",
  subject: "runtime",
  category: "architecture",
  scope_type: "platform",
  scope_id: null,
  subject_ref: null,
  question: "Which runtime?",
  alternatives: [],
  outcome: "adopt",
  rationale: "Evidence supports the selected option.",
  evidence_refs: [],
  evaluation_refs: [],
  finding_refs: [],
  cost_resource_refs: [],
  actor_ref: "user:test",
  reviewer_refs: [],
  approval_ref: null,
  adr_ref: null,
  effective_at: "2026-09-08T00:00:00+00:00",
  review_at: null,
  review_condition: null,
  status: "current",
  supersedes: null,
  superseded_by: null,
  withdrawn_at: null,
  withdrawal_reason: null,
  downstream_refs: [],
  revision: 1,
  content_digest: "digest",
  created_at: "2026-09-08T00:00:00+00:00",
  schema_version: "1.0",
  revisit_due: false,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("#598 canonical Decision Record frontend client", () => {
  it("lists/searches/filters and reads Decision Record detail through the Control Plane", async () => {
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [decision], next_cursor: null, total: 1, limit: 50 }))
      .mockResolvedValueOnce(jsonResponse(decision));
    const client = new DecisionRecordClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.list({ q: "runtime", filters: { status: "current", scope_type: "platform" } });
    await client.get("decision_record_test");

    const listUrl = new URL(fetchSpy.mock.calls[0][0] as string, "https://platform.invalid");
    expect(listUrl.pathname).toBe("/api/v1/decision-records");
    expect(listUrl.searchParams.get("q")).toBe("runtime");
    expect(listUrl.searchParams.get("filter[status]")).toBe("current");
    expect(listUrl.searchParams.get("filter[scope_type]")).toBe("platform");

    const detailUrl = new URL(fetchSpy.mock.calls[1][0] as string, "https://platform.invalid");
    expect(detailUrl.pathname).toBe("/api/v1/decision-records/decision_record_test");
    for (const [, init] of fetchSpy.mock.calls as Array<[string, RequestInit]>) {
      expect(init.method).toBe("GET");
      expect(init.credentials).toBe("include");
    }
  });
});
