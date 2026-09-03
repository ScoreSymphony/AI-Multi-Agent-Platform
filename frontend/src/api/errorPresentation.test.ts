import { describe, expect, it } from "vitest";
import { ControlPlaneError } from "./client";
import { describeError } from "./errorPresentation";

function controlPlaneError(
  status: number,
  body: Partial<ConstructorParameters<typeof ControlPlaneError>[1]> = {},
) {
  return new ControlPlaneError(status, {
    code: "forbidden",
    category: "authorization",
    message: "operation denied",
    request_id: "request_test",
    correlation_id: "correlation_test",
    retryable: false,
    ...body,
  });
}

describe("canonical frontend error presentation", () => {
  it("keeps unauthenticated distinct from unauthorized", () => {
    const presentation = describeError(
      controlPlaneError(401, { code: "unauthorized", message: "session expired" }),
    );
    expect(presentation.title).toBe("Authentication required");
    expect(presentation.message).toBe("session expired");
    expect(presentation.hint).toContain("unauthenticated");
  });

  it("surfaces approval-required outcomes without bypassing the approval service", () => {
    const presentation = describeError(
      controlPlaneError(403, {
        message: "action requires approval by local policy",
        details: {
          authorization_outcome: "require_approval",
          approval_id: "approval_123e4567-e89b-42d3-a456-426614174030",
        },
      }),
    );
    expect(presentation.title).toBe("Approval required");
    expect(presentation.reference).toBe("approval_123e4567-e89b-42d3-a456-426614174030");
    expect(presentation.hint).toContain("cannot bypass");
  });

  it("renders an ordinary authorization denial separately", () => {
    const presentation = describeError(controlPlaneError(403));
    expect(presentation.title).toBe("Access denied");
    expect(presentation.reference).toBeUndefined();
  });
});
