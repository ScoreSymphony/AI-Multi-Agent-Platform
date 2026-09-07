import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { PlanCoordinationProjection } from "../api/workflowProgress";
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
      wait_type: null,
      wait_deadline_at: null,
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
      latest_run_id: null,
      current_attempt: 2,
      retry_due_at: "2026-09-08T12:05:00+00:00",
      wait_type: "external_job",
      wait_deadline_at: "2026-09-08T12:00:00+00:00",
      reconciliation: "run_reconciled",
      reconciliation_detail: "canonical Run reconciled after operator repair",
    },
  ],
};

describe("WorkflowProgress", () => {
  it("renders canonical dependency, wait, retry and reconciliation state as a table", () => {
    const markup = renderToStaticMarkup(<WorkflowProgress projection={projection} />);

    expect(markup).toContain("plan_421");
    expect(markup).toContain("step_a");
    expect(markup).toContain("step_b");
    expect(markup).toContain("1/1 satisfied");
    expect(markup).toContain("external_job");
    expect(markup).toContain("canonical Run reconciled after operator repair");
    expect(markup).toContain("<table>");
    expect(markup).toContain("Retrying / retried");
  });
});
