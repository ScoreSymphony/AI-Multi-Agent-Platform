import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CanonicalGoal } from "../api/goals";
import { RouterProvider } from "../app/router";
import { GoalTable } from "./GoalsPage";

const goal: CanonicalGoal = {
  id: "goal_123e4567-e89b-42d3-a456-426614174000",
  type: "goal",
  version: "4",
  goal_id: "goal_123e4567-e89b-42d3-a456-426614174000",
  title: "Maintain verified quality",
  objective: "Keep the explicit quality criterion satisfied",
  owner_ref: { type: "user", id: "goal-owner" },
  project_id: "project_goal",
  created_at: "2026-09-08T06:00:00Z",
  updated_at: "2026-09-08T07:00:00Z",
  revision: 4,
  digest: "sha256:goal-r4",
  status: "waiting",
  progress: "partial",
  success_criteria: [
    {
      criterion_id: "quality",
      kind: "metric",
      description: "Quality reaches the required threshold",
      operator: "gte",
      target: 90,
      required: true,
    },
  ],
  constraints: {
    requirements: [],
    out_of_scope: [],
    risk_requirements: [],
    data_requirements: [],
    security_requirements: [],
  },
  observation_policy: {
    automation_id: "automation_goal",
    review_interval_seconds: 3600,
    event_types: [],
  },
  task_generation_policy: {
    enabled: true,
    task_title: "Restore quality",
    proposal_required: false,
  },
  autonomy_policy: {
    max_tasks_per_review: 1,
    max_consecutive_failed_cycles: 3,
    human_checkpoint_required: false,
  },
  deadline: null,
  linked_tasks: [
    {
      task_id: "task_goal",
      goal_revision: 4,
      review_id: "goal_review_1",
      task_state: "active",
      valid_for_current_revision: true,
      created_at: "2026-09-08T06:30:00Z",
    },
  ],
  active_task_ids: ["task_goal"],
  evidence: [],
  reviews: [],
  consecutive_failed_cycles: 0,
  next_review_at: "2026-09-08T08:00:00Z",
  terminal_reason: null,
  created_actor_ref: "user:goal-owner",
  updated_actor_ref: "automation:goal-review",
  stream_revision: 7,
};

function renderGoalTable(): string {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { pathname: "/goals" } },
  });
  try {
    return renderToStaticMarkup(
      <RouterProvider>
        <GoalTable goals={[goal]} />
      </RouterProvider>,
    );
  } finally {
    if (originalWindow) {
      Object.defineProperty(globalThis, "window", originalWindow);
    } else {
      Reflect.deleteProperty(globalThis, "window");
    }
  }
}

describe("Goal inventory rendering", () => {
  it("shows canonical lifecycle, progress, revision and active Task count", () => {
    const markup = renderGoalTable();

    expect(markup).toContain(goal.id);
    expect(markup).toContain(goal.title);
    expect(markup).toContain("waiting");
    expect(markup).toContain("partial");
    expect(markup).toContain(">4</td>");
    expect(markup).toContain(">1</td>");
    expect(markup).toContain(`/goals/${goal.id}`);
  });
});
