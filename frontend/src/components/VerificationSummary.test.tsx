import type { AnchorHTMLAttributes } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type {
  CanonicalVerification,
  CanonicalVerificationRequirement,
} from "../api/verification";
import { VerificationSummaryView } from "./VerificationSummary";

vi.mock("../app/router", () => ({
  AppLink: (props: AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props} />,
}));

const requirement: CanonicalVerificationRequirement = {
  id: "task_test",
  type: "verification_requirement",
  task_id: "task_test",
  policy: { id: "verification_policy_test", version: 2 },
  subject: {
    type: "result",
    id: "result_test",
    revision: "3",
    digest: "sha256:exact-result",
  },
  created_at: "2026-09-04T00:00:00+00:00",
  updated_at: "2026-09-04T00:01:00+00:00",
  completion: {
    state: "waiting",
    reason: "required verification has not passed",
    blocking_verification_ids: ["verification_test"],
    repair_attempts_remaining: 1,
  },
};

const verification: CanonicalVerification = {
  id: "verification_test",
  type: "verification",
  task_id: "task_test",
  run_id: "run_test",
  result_id: "result_test",
  artifact_ids: ["artifact_evidence"],
  project_id: "project_test",
  capability_ids: [],
  policy: { id: "verification_policy_test", version: 2 },
  stage_id: "human-review",
  subject: {
    type: "result",
    id: "result_test",
    revision: "3",
    digest: "sha256:exact-result",
  },
  requested_verifier_kind: "human",
  requested_capability_ref: null,
  repair_attempt: 0,
  status: "completed",
  created_at: "2026-09-04T00:00:00+00:00",
  expires_at: null,
  correlation_id: "corr-test",
  causation_id: null,
  verification_result: {
    id: "verification_result_test",
    verification_id: "verification_test",
    outcome: "pass",
    subject: {
      type: "result",
      id: "result_test",
      revision: "3",
      digest: "sha256:exact-result",
    },
    verifier: {
      ref: "user:reviewer",
      kind: "human",
      agent_id: null,
      agent_revision: null,
      model_config_id: null,
      provider_id: null,
      read_only: true,
    },
    findings: [],
    evidence_artifact_ids: ["artifact_evidence"],
    checks_executed: ["human_review"],
    errors: [],
    started_at: "2026-09-04T00:00:30+00:00",
    completed_at: "2026-09-04T00:01:00+00:00",
    metadata: {},
  },
};

describe("#86 verification summary", () => {
  it("renders task completion policy and exact linked verification history", () => {
    const markup = renderToStaticMarkup(
      <VerificationSummaryView
        error={null}
        loading={false}
        requirement={requirement}
        scope={{ kind: "task", id: "task_test" }}
        verifications={[verification]}
      />,
    );

    expect(markup).toContain("Verification");
    expect(markup).toContain("waiting");
    expect(markup).toContain("Policy");
    expect(markup).toContain("v2");
    expect(markup).toContain("sha256:exact-result");
    expect(markup).toContain('href="/verification/verification_test"');
    expect(markup).toContain("pass");
    expect(markup).toContain("revision 3");
  });

  it("renders an explicit empty state when a Run has no canonical verification", () => {
    const markup = renderToStaticMarkup(
      <VerificationSummaryView
        error={null}
        loading={false}
        requirement={null}
        scope={{ kind: "run", id: "run_test" }}
        verifications={[]}
      />,
    );

    expect(markup).toContain("No verification recorded");
    expect(markup).toContain("this run");
  });
});
