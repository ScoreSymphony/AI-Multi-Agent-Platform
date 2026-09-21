import { type AnchorHTMLAttributes, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { OnboardingStatus } from "../api/onboarding";
import { MultiAgentFirstResult } from "./OnboardingPage";
import { MultiAgentGoalForm } from "./onboarding/forms";

vi.mock("../app/router", () => ({
  AppLink: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { children?: ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
  matchPath: () => null,
  useRouter: () => ({ path: "/", navigate: vi.fn() }),
}));

function status(overrides: Partial<OnboardingStatus> = {}): OnboardingStatus {
  return {
    id: "first-run",
    type: "onboarding_status",
    state: "needs_general_assistant",
    authenticated_actor_present: true,
    project_count: 2,
    workspace_count: 1,
    local_model_count: 1,
    self_hosted_model_count: 0,
    remote_model_count: 0,
    text_capable_golden_path_model_count: 1,
    usable_golden_path_model_count: 1,
    general_assistant_count: 0,
    executable_general_assistant_count: 0,
    general_assistant_blockers: [],
    selection_required: false,
    selection_kind: null,
    candidate_project_ids: ["project-a", "project-b"],
    candidate_workspace_ids: ["workspace-only"],
    candidate_agent_ids: [],
    starter_catalog_installed: false,
    installed_model_adapter_ids: ["adapter-test"],
    automatic_remote_provider_selection: false,
    automatic_paid_provider_selection: false,
    guidance: [],
    ...overrides,
  };
}

describe("official multi-agent onboarding presentation", () => {
  it("keeps the canonical Workspace explicit even when there is only one candidate", () => {
    const html = renderToStaticMarkup(
      <MultiAgentGoalForm status={status()} busy={false} onSubmit={() => undefined} />,
    );

    expect(html).toContain('name="workspace_id"');
    expect(html).toContain("workspace-only");
  });

  it("renders dependency, artifact, result and verification trace from the shared API response", () => {
    const html = renderToStaticMarkup(
      <MultiAgentFirstResult
        result={{
          id: "task-1",
          type: "multi_agent_first_run_result",
          workflow: "reference-multi-agent",
          task_id: "task-1",
          task_status: "succeeded",
          plan_id: "plan-1",
          project_id: "project-1",
          workspace_id: "workspace-1",
          agents: {
            researcher: { agent_id: "agent-r", revision: 1 },
            developer: { agent_id: "agent-d", revision: 1 },
            reviewer: { agent_id: "agent-v", revision: 1 },
          },
          steps: [
            {
              step_id: "step-research",
              title: "Gather authoritative evidence",
              status: "succeeded",
              phase: "terminal",
              depends_on: [],
              satisfied_dependencies: [],
              agent_id: "agent-r",
              agent_revision: 1,
              run_id: "run-r",
              run_status: "succeeded",
              result_ids: ["result-r"],
              artifact_ids: [],
            },
            {
              step_id: "step-execute",
              title: "Produce the requested result",
              status: "succeeded",
              phase: "terminal",
              depends_on: ["step-research", "step-approach"],
              satisfied_dependencies: ["step-research", "step-approach"],
              agent_id: "agent-d",
              agent_revision: 1,
              run_id: "run-e",
              run_status: "succeeded",
              result_ids: ["result-final"],
              artifact_ids: [],
            },
          ],
          result_id: "result-final",
          result_ids: ["result-r", "result-final"],
          artifact_ids: ["artifact-goal"],
          review: {
            step_status: "passed",
            verification_status: "pass",
            verification_id: "verification-final",
          },
          verification: [
            {
              verification_id: "verification-final",
              stage_id: "official-first-run-result-review",
              status: "completed",
              subject_type: "result",
              subject_id: "result-final",
              outcome: "pass",
              is_final_result_review: true,
            },
          ],
          trace: { task_id: "task-1", plan_id: "plan-1", step_ids: ["step-research", "step-execute"] },
        }}
      />,
    );

    expect(html).toContain("Official multi-agent first-run result");
    expect(html).toContain("Participating roles");
    expect(html).toContain("developer · researcher · reviewer");
    expect(html).toContain("parallel root");
    expect(html).toContain("step-approach");
    expect(html).toContain("/workspaces/workspace-1");
    expect(html).toContain("Open Workspace");
    expect(html).toContain("/results/result-final");
    expect(html).toContain("/artifacts/artifact-goal");
    expect(html).toContain("verification-final");
    expect(html).toContain("produced result");
  });
});
