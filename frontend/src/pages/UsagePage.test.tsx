import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlPlaneError } from "../api/client";
import type { APIErrorBody } from "../api/types";
import { UsageFailureState } from "./UsagePage";

function controlPlaneError(status: number, code: string, message: string, retryable = false) {
  return new ControlPlaneError(status, {
    code,
    category: code === "forbidden" ? "authorization" : "backend",
    message,
    retryable,
    details: {},
    request_id: "request_123",
    correlation_id: "correlation_123",
  } as APIErrorBody);
}

describe("Usage failure presentation", () => {
  it("preserves permission-denied semantics per failed accounting collection", () => {
    const html = renderToStaticMarkup(
      <UsageFailureState
        failures={[{
          collection: "usage-budgets",
          error: controlPlaneError(403, "forbidden", "Budget visibility denied"),
        }]}
        onRetry={vi.fn()}
      />,
    );

    expect(html).toContain("usage-budgets");
    expect(html).toContain("Access denied");
    expect(html).toContain("Budget visibility denied");
    expect(html).not.toContain(">Retry</button>");
  });

  it("preserves unavailable/retryable semantics without hiding healthy collections", () => {
    const html = renderToStaticMarkup(
      <UsageFailureState
        failures={[{
          collection: "usage-records",
          error: controlPlaneError(503, "unavailable", "Accounting backend offline", true),
        }]}
        onRetry={vi.fn()}
      />,
    );

    expect(html).toContain("Subsystem unavailable");
    expect(html).toContain("Accounting backend offline");
    expect(html).toContain("reports this failure as retryable");
    expect(html).toContain("Available accounting collections remain usable");
    expect(html).toContain(">Retry</button>");
  });
});
