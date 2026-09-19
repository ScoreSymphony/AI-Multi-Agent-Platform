import { renderToStaticMarkup } from "react-dom/server";
import { ControlPlaneError } from "../../api/client";
import { describe, expect, it, vi } from "vitest";
import { GoalClient, type CanonicalGoal } from "../../api/goals";
import type { Page } from "../../api/types";
import { GoalInventoryState, GoalsPage } from "./GoalsPage";

function retryableBackendError(message: string): ControlPlaneError {
  return new ControlPlaneError(503, {
    code: "unavailable",
    category: "backend",
    message,
    request_id: "request_regression_retry",
    correlation_id: "correlation_regression_retry",
    retryable: true,
  });
}

function page(items: CanonicalGoal[] = []): Page<CanonicalGoal> {
  return { items, next_cursor: null, total: items.length, limit: 50 };
}

describe("Goals route regression coverage", () => {
  it("preserves route composition, initial loading and accessible create controls", () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("SSR regression coverage must not perform network requests");
    }) as unknown as typeof fetch;
    const html = renderToStaticMarkup(<GoalsPage client={new GoalClient({ fetchImpl })} />);

    expect(html).toContain("<h1>Goals</h1>");
    expect(html).toContain("Goal inventory");
    expect(html).toContain("Loading…");
    expect(html).toContain("Create Goal");
    expect(html).toContain("Success criteria JSON");
    expect(html).toContain('type="submit"');
    expect(html).toContain("Create draft Goal");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("maps loading, backend error and empty inventory states deterministically", () => {
    const retry = vi.fn();
    expect(renderToStaticMarkup(
      <GoalInventoryState page={null} error={null} onRetry={retry} />,
    )).toContain("Loading…");

    const errorMarkup = renderToStaticMarkup(
      <GoalInventoryState page={null} error={retryableBackendError("goal service unavailable")} onRetry={retry} />,
    );
    expect(errorMarkup).toContain('role="alert"');
    expect(errorMarkup).toContain("goal service unavailable");
    expect(errorMarkup).toContain(">Retry</button>");

    expect(renderToStaticMarkup(
      <GoalInventoryState page={page()} error={null} onRetry={retry} />,
    )).toContain("No Goals configured");

    const staleMarkup = renderToStaticMarkup(
      <GoalInventoryState page={page()} error={new Error("refresh failed")} onRetry={retry} />,
    );
    expect(staleMarkup).toContain("refresh failed");
    expect(staleMarkup).toContain("No Goals configured");
  });
});
