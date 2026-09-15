import { type AnchorHTMLAttributes, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { MultiAgentFirstResult } from "./OnboardingPage";

vi.mock("../app/router", () => ({
  AppLink: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { children?: ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
  matchPath: () => null,
  useRouter: () => ({ path: "/", navigate: vi.fn() }),
}));

describe("official multi-agent onboarding presentation", () => {
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
    expect(html).toContain("parallel root");
    expect(html).toContain("step-approach");
    expect(html).toContain("/results/result-final");
    expect(html).toContain("/artifacts/artifact-goal");
    expect(html).toContain("verification-final");
    expect(html).toContain("produced result");
  });
});
