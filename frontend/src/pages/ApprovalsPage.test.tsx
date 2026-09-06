import { renderToStaticMarkup } from "react-dom/server";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";
import type { CanonicalApproval } from "../api/approvals";
import type { ApprovalDecisionManifestState } from "../app/approvalManifest";
import { RouterProvider } from "../app/router";
import { ApprovalDetailView } from "./ApprovalsPage";

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

function renderApprovalDetail(
  approval: CanonicalApproval,
  decisionState: ApprovalDecisionManifestState,
  confirmed = false,
): string {
  return renderWithRouter(
    <ApprovalDetailView
      approval={approval}
      decisionState={decisionState}
      loadError={null}
      decisionError={null}
      comment=""
      confirmed={confirmed}
      busy={false}
      onRetry={() => undefined}
      onComment={() => undefined}
      onConfirmed={() => undefined}
      onDecision={async () => undefined}
    />,
  );
}

function renderWithRouter(element: ReactElement): string {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { pathname: "/approvals" } },
  });
  try {
    return renderToStaticMarkup(<RouterProvider>{element}</RouterProvider>);
  } finally {
    if (originalWindow) {
      Object.defineProperty(globalThis, "window", originalWindow);
    } else {
      Reflect.deleteProperty(globalThis, "window");
    }
  }
}

describe("Approval detail rendering", () => {
  it("keeps exact read-only inspection visible when decision capability is unavailable", () => {
    const markup = renderApprovalDetail(pendingApproval, "unavailable");

    expect(markup).toContain(pendingApproval.id);
    expect(markup).toContain(pendingApproval.action);
    expect(markup).toContain(pendingApproval.resource_id);
    expect(markup).toContain(pendingApproval.policy_id);
    expect(markup).toContain(pendingApproval.requested_action_digest);
    expect(markup).toContain("Read-only Approval surface");
    expect(markup).not.toContain(">Approve</button>");
    expect(markup).not.toContain(">Deny</button>");
  });

  it("never renders secret-bearing proposed payload fields even if present on an input object", () => {
    const approvalWithUnexpectedSecretFields = {
      ...pendingApproval,
      proposed_payload: {
        operation: "sensitive-test",
        secret_token: "never-render-this-secret",
      },
      secret: "never-render-this-secret",
    } as CanonicalApproval & {
      proposed_payload: Record<string, string>;
      secret: string;
    };

    const markup = renderApprovalDetail(approvalWithUnexpectedSecretFields, "available");

    expect(markup).not.toContain("never-render-this-secret");
    expect(markup).not.toContain("secret_token");
    expect(markup).toContain(pendingApproval.requested_action_digest);
  });

  it("requires explicit confirmation before Approve and Deny become actionable", () => {
    const unconfirmed = renderApprovalDetail(pendingApproval, "available", false);
    const confirmed = renderApprovalDetail(pendingApproval, "available", true);

    expect(unconfirmed.match(/<button disabled="">/g)).toHaveLength(2);
    expect(confirmed).toContain("<button>Approve</button>");
    expect(confirmed).toContain("<button>Deny</button>");
  });

  it("renders a terminal Approval as read-only after a canonical decision", () => {
    const approved: CanonicalApproval = {
      ...pendingApproval,
      status: "approved",
      decision_by: { type: "user", id: "approver" },
      decision_at: "2026-09-05T20:10:00Z",
      decision_comment: "Reviewed exact action",
    };

    const markup = renderApprovalDetail(approved, "available");

    expect(markup).toContain("Approval is approved");
    expect(markup).toContain("user:approver");
    expect(markup).not.toContain(">Approve</button>");
    expect(markup).not.toContain(">Deny</button>");
  });
});
