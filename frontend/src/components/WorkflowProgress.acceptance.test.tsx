import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type {
  PlanCoordinationProjection,
  PlanCoordinationStep,
} from "../api/workflowProgress";
import { RouterProvider } from "../app/router";
import { WorkflowProgress } from "./WorkflowProgress";

function step(overrides: Partial<PlanCoordinationStep> & Pick<PlanCoordinationStep, "id">): PlanCoordinationStep {
  return {
    status: "pending",
    coordination_phase: "blocked",
    coordination_revision: 1,
    dependency_ids: [],
    satisfied_dependency_ids: [],
    latest_run_id: null,
    current_attempt: 0,
    retry_due_at: null,
    retry_state: "none",
    retry_max_attempts: 1,
    wait_key: null,
    wait_type: null,
    wait_state: null,
    wait_deadline_at: null,
    wait_resolved_at: null,
    wait_approval_id: null,
    wait_approval_subject_type: null,
    wait_approval_subject_id: null,
    wait_approval_action: null,
    wait_event_type: null,
    wait_correlation_key: null,
    wait_external_job_ref: null,
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

function renderWorkflow(value: PlanCoordinationProjection): string {
  vi.stubGlobal("window", { location: { pathname: "/" } });
  try {
    return renderToStaticMarkup(
      <RouterProvider>
        <WorkflowProgress projection={value} />
      </RouterProvider>,
    );
  } finally {
    vi.unstubAllGlobals();
  }
}

describe("WorkflowProgress acceptance scenarios", () => {
  it("renders linear progression from the canonical Step sequence", () => {
    const markup = renderWorkflow(
      projection([
        step({
          id: "step_linear_done",
          status: "succeeded",
          coordination_phase: "terminal",
          current_attempt: 1,
        }),
        step({
          id: "step_linear_running",
          status: "running",
          coordination_phase: "attempt_active",
          dependency_ids: ["step_linear_done"],
          satisfied_dependency_ids: ["step_linear_done"],
          latest_run_id: "run_linear_running",
          current_attempt: 1,
        }),
      ]),
    );

    expect(markup).toContain("step_linear_done");
    expect(markup).toContain("step_linear_running");
    expect(markup).toContain("1/1 satisfied");
    expect(markup).toContain("run_linear_running");
  });

  it("renders diamond fan-in dependency satisfaction and an exact approval wait", () => {
    const markup = renderWorkflow(
      projection([
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
          wait_key: "wait_approval_42",
          wait_type: "approval",
          wait_state: "active",
          wait_deadline_at: "2026-09-08T12:00:00+00:00",
          wait_approval_id: "approval_42",
          wait_approval_subject_type: "step",
          wait_approval_subject_id: "step_right",
          wait_approval_action: "repository:write",
        }),
        step({
          id: "step_join",
          status: "pending",
          coordination_phase: "blocked",
          dependency_ids: ["step_left", "step_right"],
          satisfied_dependency_ids: ["step_left"],
        }),
      ]),
    );

    expect(markup).toContain("step_join");
    expect(markup).toContain("1/2 satisfied");
    expect(markup).toContain("approval_42");
    expect(markup).toContain("repository:write");
    expect(markup).toContain("subject step:step_right");
  });

  it("renders safe Event and external-job summaries plus expired deadline state", () => {
    const markup = renderWorkflow(
      projection([
        step({
          id: "step_event",
          status: "waiting",
          coordination_phase: "waiting",
          current_attempt: 1,
          wait_key: "wait_event_560",
          wait_type: "event",
          wait_state: "active",
          wait_event_type: "connector.completed",
          wait_correlation_key: "corr_event_560",
        }),
        step({
          id: "step_external",
          status: "waiting",
          coordination_phase: "waiting",
          current_attempt: 1,
          wait_key: "wait_external_560",
          wait_type: "external_job",
          wait_state: "active",
          wait_external_job_ref: "adapter-job-560",
        }),
        step({
          id: "step_deadline",
          status: "failed",
          coordination_phase: "terminal",
          current_attempt: 1,
          wait_key: "wait_deadline_560",
          wait_type: "deadline",
          wait_state: "expired",
          wait_deadline_at: "2026-09-08T12:00:00+00:00",
          wait_resolved_at: "2026-09-08T12:00:01+00:00",
        }),
      ]),
    );

    expect(markup).toContain("connector.completed");
    expect(markup).toContain("corr_event_560");
    expect(markup).toContain("adapter-job-560");
    expect(markup).toContain("expired");
    expect(markup).toContain("deadline");
    expect(markup).toContain("resolved");
  });

  it("keeps resolved wait identity and resolution visible from the canonical projection", () => {
    const markup = renderWorkflow(
      projection([
        step({
          id: "step_review",
          status: "failed",
          coordination_phase: "terminal",
          current_attempt: 1,
          wait_key: "wait_review",
          wait_type: "approval",
          wait_state: "rejected",
          wait_resolved_at: "2026-09-08T12:01:00+00:00",
          wait_approval_id: "approval_review",
          wait_approval_subject_type: "step",
          wait_approval_subject_id: "step_review",
          wait_approval_action: "repository:merge",
        }),
      ]),
    );

    expect(markup).toContain("rejected");
    expect(markup).toContain("approval_review");
    expect(markup).toContain("repository:merge");
    expect(markup).toContain("resolved");
  });

  it("distinguishes scheduled, active, exhausted and non-retryable retry states", () => {
    const markup = renderWorkflow(
      projection([
        step({
          id: "step_scheduled",
          status: "failed",
          coordination_phase: "retry_scheduled",
          current_attempt: 1,
          retry_state: "scheduled",
          retry_due_at: "2026-09-08T12:05:00+00:00",
          retry_max_attempts: 3,
        }),
        step({
          id: "step_active",
          status: "running",
          coordination_phase: "attempt_active",
          current_attempt: 2,
          retry_state: "active",
          retry_max_attempts: 3,
        }),
        step({
          id: "step_exhausted",
          status: "failed",
          coordination_phase: "terminal",
          latest_run_id: "run_exhausted",
          current_attempt: 3,
          retry_state: "exhausted",
          retry_max_attempts: 3,
        }),
        step({
          id: "step_fatal",
          status: "failed",
          coordination_phase: "terminal",
          current_attempt: 1,
          retry_state: "not_retryable",
          retry_max_attempts: 3,
        }),
      ]),
    );

    expect(markup).toContain("scheduled");
    expect(markup).toContain("active");
    expect(markup).toContain("exhausted");
    expect(markup).toContain("not_retryable");
    expect(markup).toContain("run_exhausted");
    expect(markup).toContain("attempt 3/3");
  });

  it("renders cancellation and reconciliation disposition from canonical fields", () => {
    const markup = renderWorkflow(
      projection([
        step({
          id: "step_cancelled",
          status: "cancelled",
          coordination_phase: "terminal",
          current_attempt: 2,
          retry_state: "cancelled",
          retry_max_attempts: 3,
          wait_key: "wait_cancelled",
          wait_type: "external_job",
          wait_state: "cancelled",
          wait_external_job_ref: "adapter-job-cancelled",
          reconciliation: "canonical_terminal",
          reconciliation_detail: "terminal canonical Run reconciled after restart",
        }),
      ]),
    );

    expect(markup).toContain("cancelled");
    expect(markup).toContain("adapter-job-cancelled");
    expect(markup).toContain("canonical_terminal");
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

    const markup = renderWorkflow(projection([unsafe]));

    expect(markup).toContain("step_safe");
    expect(markup).not.toContain("lease-secret-421");
    expect(markup).not.toContain("owner-secret-421");
    expect(markup).not.toContain("temporal-private-421");
    expect(markup).not.toContain("provider-secret-421");
  });
});
