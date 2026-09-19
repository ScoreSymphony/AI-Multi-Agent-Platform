import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlPlaneError } from "../api/client";
import { ErrorState } from "./States";

function canonicalError(retryable: boolean) {
  return new ControlPlaneError(retryable ? 503 : 403, {
    code: retryable ? "unavailable" : "forbidden",
    category: retryable ? "provider" : "authorization",
    message: retryable ? "provider unavailable" : "operation denied",
    request_id: "request_state_test",
    correlation_id: "correlation_state_test",
    retryable,
  });
}

describe("cross-cutting async state presentation", () => {
  it("offers retry only when the canonical error contract marks the failure retryable", () => {
    const retry = vi.fn();
    const retryableMarkup = renderToStaticMarkup(
      <ErrorState error={canonicalError(true)} onRetry={retry} />,
    );
    const deniedMarkup = renderToStaticMarkup(
      <ErrorState error={canonicalError(false)} onRetry={retry} />,
    );

    expect(retryableMarkup).toContain(">Retry<");
    expect(deniedMarkup).not.toContain(">Retry<");
    expect(deniedMarkup).toContain("Access denied");
  });

  it("does not invent retry semantics for local validation/programming errors", () => {
    const markup = renderToStaticMarkup(
      <ErrorState error={new Error("invalid local route")} onRetry={vi.fn()} />,
    );
    expect(markup).not.toContain(">Retry<");
  });
});
