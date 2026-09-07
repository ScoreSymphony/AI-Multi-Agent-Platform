import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { PlanCoordinationProjection } from "../api/workflowProgress";
import { RouterProvider } from "../app/router";
import { WorkflowProgress } from "./WorkflowProgress";

const projection: PlanCoordinationProjection = {
  id: "plan_421",
  task_id: "task_421",
  plan_revision: 7,
  steps: [
    {
      id: "step_a",
      status: "succeeded",
      coordination_phase: "terminal",
      coordination_revision: 2,
      dependency_ids: [],
      satisfied_dependency_ids: [],
      latest_run_id: null,
      current_attempt: 1,
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
    },
    {
      id: "step_b",
      status: "waiting",
      coordination_phase: "waiting",
      coordination_revision: 4,
      dependency_ids: ["step_a"],
      satisfied_dependency_ids: ["step_a"],
      latest_run_id: "run_retry",
      current_attempt: 2,
      retry_due_at: null,
      retry_state: "active",
      retry_max_attempts: 3,
      wait_key: "wait_job_1",
      wait_type: "external_job",
      wait_state: "active",
      wait_deadline_at: "2026-09-08T12:00:00+00:00",
      wait_resolved_at: null,
      wait_approval_id: null,
      wait_approval_subject_type: null,
      wait_approval_subject_id: null,
      wait_approval_action: null,
      wait_event_type: null,
      wait_correlation_key: null,
      wait_external_job_ref: "adapter-job-42",
      reconciliation: "run_reconciled",
      reconciliation_detail: "canonical Run reconciled after operator repair",
    },
  ],
};

describe("WorkflowProgress", () => {
  it("renders canonical dependency, safe wait, retry and reconciliation state as a table", () => {
    vi.stubGlobal("window", { location: { pathname: "/" } });
    try {
      const markup = renderToStaticMarkup(
        <RouterProvider>
          <WorkflowProgress projection={projection} />
        </RouterProvider>,
      );

      expect(markup).toContain("plan_421");
      expect(markup).toContain("step_a");
      expect(markup).toContain("step_b");
      expect(markup).toContain("1/1 satisfied");
      expect(markup).toContain("external_job");
      expect(markup).toContain("adapter-job-42");
      expect(markup).toContain("active");
      expect(markup).toContain("canonical Run reconciled after operator repair");
      expect(markup).toContain("<table>");
      expect(markup).toContain("Retry state");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
