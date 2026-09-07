import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type {
  PlanCoordinationProjection,
  PlanCoordinationStep,
} from "../api/workflowProgress";
import { WorkflowProgress } from "./WorkflowProgress";

function step(overrides: Partial<PlanCoordinationStep> & Pick<PlanCoordinationStep, "id">): PlanCoordinationStep {
  return {
    id: overrides.id,
    status: "pending",
    coordination_phase: "blocked",
    coordination_revision: 1,
    dependency_ids: [],
    satisfied_dependency_ids: [],
    latest_run_id: null,
    current_attempt: 0,
    retry_due_at: null,
    wait_type: null,
    wait_deadline_at: null,
    reconciliation: "consistent",
    reconciliation_detail: null,
    ...overrides,
  };
}

function projection(steps: PlanCoordinationStep[]): PlanCoordinationProjection {
  return {
    id: "plan_421",
    task_id: "task_421",
    plan_revision: 9,
    steps,
  };
}

describe("WorkflowProgress acceptance scenarios", () => {
  it("renders diamond fan-in dependency satisfaction and an approval wait with deadline", () => {
    const markup = renderToStaticMarkup(
      <WorkflowProgress
        projection={projection([
          step({ id: "step_root", status: "succeeded", coordination_phase: "terminal", current_attempt: 1 }),
          step({
            id: "step_left",
            status: "succeeded",
            coordination_phase: "terminal",
            dependency_ids: ["step_root"],
            satisfied_dependency_ids: ["step_root"],
            current_attempt: 1,
          }),
          step({
            id: "step_right",
            status: "waiting",
            coordination_phase: "waiting",
            dependency_ids: ["step_root"],
            satisfied_dependency_ids: ["step_root"],
            current_attempt: 1,
            wait_type: "approval",
            wait_deadline_at: "2026-09-08T12:00:00+00:00",
          }),
          step({
            id: "step_join",
            status: "pending",
            coordination_phase: "blocked",
            dependency_ids: ["step_left", "step_right"],
            satisfied_dependency_ids: ["step_left"],
          }),
        ])}
      />,
    );

    expect(markup).toContain("step_join");
    expect(markup).toContain("1/2 satisfied");
    expect(markup).toContain("approval");
    expect(markup).toContain("step_right");
  });

  it("reflects approval resolution from the next canonical projection instead of retaining client state", () => {
    const waiting = renderToStaticMarkup(
      <WorkflowProgress
        projection={projection([
          step({
            id: "step_review",
            status: "waiting",
            coordination_phase: "waiting",
            current_attempt: 1,
            wait_type: "approval",
          }),
        ])}
      />,
    );
    const resolved = renderToStaticMarkup(
      <WorkflowProgress
        projection={projection([
          step({
            id: "step_review",
            status: "succeeded",
            coordination_phase: "terminal",
            current_attempt: 1,
            wait_type: null,
          }),
        ])}
      />,
    );

    expect(waiting).toContain("approval");
    expect(resolved).toContain("succeeded");
    expect(resolved).not.toContain(">approval<");
  });

  it("renders cancellation, terminal retry history and reconciliation disposition from canonical fields", () => {
    const markup = renderToStaticMarkup(
      <WorkflowProgress
        projection={projection([
          step({
            id: "step_cancelled",
            status: "cancelled",
            coordination_phase: "terminal",
            current_attempt: 1,
            reconciliation: "canonical_terminal",
          }),
          step({
            id: "step_exhausted",
            status: "failed",
            coordination_phase: "terminal",
            latest_run_id: "run_exhausted",
            current_attempt: 3,
            retry_due_at: null,
            reconciliation: "run_reconciled",
            reconciliation_detail: "terminal canonical Run reconciled after restart",
          }),
        ])}
      />,
    );

    expect(markup).toContain("cancelled");
    expect(markup).toContain("canonical_terminal");
    expect(markup).toContain("failed");
    expect(markup).toContain("run_exhausted");
    expect(markup).toContain("previous retry");
    expect(markup).toContain("run_reconciled");
    expect(markup).toContain("terminal canonical Run reconciled after restart");
  });

  it("renders only the stable projection allowlist and ignores backend-private extras", () => {
    const unsafe = {
      ...step({ id: "step_safe", status: "waiting", coordination_phase: "waiting" }),
      lease_token: "lease-secret-421",
      coordinator_owner_token: "owner-secret-421",
      backend_workflow_id: "temporal-private-421",
      raw_provider_payload: "provider-secret-421",
    } as PlanCoordinationStep;

    const markup = renderToStaticMarkup(
      <WorkflowProgress projection={projection([unsafe])} />,
    );

    expect(markup).toContain("step_safe");
    expect(markup).not.toContain("lease-secret-421");
    expect(markup).not.toContain("owner-secret-421");
    expect(markup).not.toContain("temporal-private-421");
    expect(markup).not.toContain("provider-secret-421");
  });
});
