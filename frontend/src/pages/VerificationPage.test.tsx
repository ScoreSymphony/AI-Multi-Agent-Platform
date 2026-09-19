import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlPlaneError } from "../api/client";
import type { APIErrorBody } from "../api/types";
import type { CanonicalVerification } from "../api/verification";
import { RouterProvider } from "../app/router";
import { VerificationDetailView } from "./VerificationPage";

const verification: CanonicalVerification = {
  id: "verification_123",
  type: "verification",
  task_id: "task_123",
  run_id: "run_123",
  result_id: "result_123",
  artifact_ids: [],
  project_id: "project_123",
  capability_ids: [],
  policy: { id: "policy_123", version: 1 },
  stage_id: "human-review",
  subject: {
    type: "result",
    id: "result_123",
    revision: "1",
    digest: "sha256:result",
  },
  requested_verifier_kind: "human",
  requested_capability_ref: null,
  repair_attempt: 0,
  status: "pending",
  created_at: "2026-09-19T10:00:00Z",
  expires_at: null,
  correlation_id: "correlation_123",
  causation_id: null,
};

function renderWithRouter(element: ReactElement): string {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { pathname: "/verification/verification_123" } },
  });
  try {
    return renderToStaticMarkup(<RouterProvider>{element}</RouterProvider>);
  } finally {
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  }
}

function denied(message: string): ControlPlaneError {
  return new ControlPlaneError(403, {
    code: "forbidden",
    category: "authorization",
    message,
    retryable: false,
    details: {},
    request_id: "request_123",
    correlation_id: "correlation_123",
  } as APIErrorBody);
}

describe("Verification detail state coverage", () => {
  it("surfaces refresh and completion-policy failures without hiding canonical review context", () => {
    const html = renderWithRouter(
      <VerificationDetailView
        verification={verification}
        requirement={null}
        history={[]}
        comment=""
        evidenceText=""
        busy={false}
        loadError={new Error("refresh failed")}
        requirementError={denied("Requirement visibility denied")}
        actionError={null}
        canReview
        onComment={vi.fn()}
        onEvidence={vi.fn()}
        onReview={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(html).toContain("refresh failed");
    expect(html).toContain("Access denied");
    expect(html).toContain("Requirement visibility denied");
    expect(html).toContain("sha256:result");
    expect(html).toContain(">Accept</button>");
  });

  it("removes review actions once canonical state is terminal", () => {
    const html = renderWithRouter(
      <VerificationDetailView
        verification={{ ...verification, status: "completed" }}
        requirement={null}
        history={[]}
        comment=""
        evidenceText=""
        busy={false}
        loadError={null}
        requirementError={null}
        actionError={new Error("stale review attempt")}
        canReview={false}
        onComment={vi.fn()}
        onEvidence={vi.fn()}
        onReview={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(html).not.toContain(">Accept</button>");
    expect(html).not.toContain(">Request changes</button>");
    expect(html).not.toContain(">Reject</button>");
  });
});
