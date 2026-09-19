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

  it("distinguishes stale deep links and validation failures from backend outages", () => {
    expect(
      describeError(controlPlaneError(404, {
        code: "not_found",
        category: "request",
        message: "resource missing",
      })).title,
    ).toBe("Not found");
    expect(
      describeError(controlPlaneError(422, {
        code: "invalid_request",
        category: "request",
        message: "invalid value",
      })).title,
    ).toBe("Validation failed");
    expect(
      describeError(controlPlaneError(409, {
        code: "conflict",
        category: "request",
        message: "revision changed",
      })).title,
    ).toBe("State changed");
  });

  it("does not misclassify an unsupported 400 response as validation", () => {
    const presentation = describeError(controlPlaneError(400, {
      code: "unsupported_capability",
      category: "contract",
      message: "semantic search unavailable",
    }));
    expect(presentation.title).toBe("Request failed");
  });

  it("labels model unavailability as a subsystem outage", () => {
    const presentation = describeError(controlPlaneError(503, {
      code: "model_unavailable",
      category: "provider",
      message: "local model is offline",
      retryable: true,
    }));
    expect(presentation.title).toBe("Subsystem unavailable");
    expect(presentation.hint).toContain("retryable");
  });

  it("labels transport failures as Control Plane availability failures", () => {
    const presentation = describeError(controlPlaneError(0, {
      code: "network_failure",
      category: "transport",
      message: "network request failed",
      retryable: true,
    }));
    expect(presentation.title).toBe("Control Plane unavailable");
    expect(presentation.hint).toContain("retried");
  });
});
