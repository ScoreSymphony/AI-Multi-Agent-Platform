import { type AnchorHTMLAttributes, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { OnboardingStatus } from "../api/onboarding";
import type { CanonicalWorkspaceIdentity } from "../api/types";
import { manifestResourceState } from "../app/Shell";
import {
  FirstResult,
  OnboardingStateSummary,
  buildConfigureModelInput,
  buildFirstRunTaskInput,
  generalAssistantCloneScope,
  onboardingStatePresentation,
} from "./OnboardingPage";

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
    state: "needs_model",
    authenticated_actor_present: true,
    project_count: 0,
    workspace_count: 0,
    local_model_count: 0,
    self_hosted_model_count: 0,
    remote_model_count: 0,
    text_capable_golden_path_model_count: 0,
    usable_golden_path_model_count: 0,
    general_assistant_count: 0,
    executable_general_assistant_count: 0,
    general_assistant_blockers: [],
    selection_required: false,
    selection_kind: null,
    candidate_project_ids: [],
    candidate_workspace_ids: [],
    candidate_agent_ids: [],
    starter_catalog_installed: false,
    installed_model_adapter_ids: ["adapter-test"],
    automatic_remote_provider_selection: false,
    automatic_paid_provider_selection: false,
    guidance: [],
    ...overrides,
  };
}

function workspace(
  id: string,
  projectId: string,
): CanonicalWorkspaceIdentity {
  return {
    id,
    type: "workspace",
    project_id: projectId,
    owner: { type: "user", id: "user-1" },
    created_at: null,
    lifecycle: "identity_only",
  };
}

describe("guided first-run onboarding", () => {
  it("has actionable browser copy for every canonical #250 state", () => {
    for (const stateName of [
      "needs_model",
      "needs_project",
      "needs_workspace",
      "needs_general_assistant",
      "needs_selection",
      "ready_for_task",
    ] as const) {
      const presentation = onboardingStatePresentation(stateName);
      expect(presentation.title.length).toBeGreaterThan(0);
      expect(presentation.detail.length).toBeGreaterThan(0);
    }
  });

  it("renders a fresh-install needs_model state with an actionable model next step", () => {
    const html = renderToStaticMarkup(
      <OnboardingStateSummary status={status({ state: "needs_model" })} />,
    );

    expect(html).toContain("needs_model");
    expect(html).toContain("Connect a local or self-hosted model");
    expect(html).toContain("Remote or paid providers are never selected automatically");
  });

  it("builds local model setup with SecretReference metadata and no secret value field", () => {
    const form = new FormData();
    form.set("adapter_id", "adapter-test");
    form.set("provider_id", "provider-local");
    form.set("model_config_id", "model-local");
    form.set("provider_model", "native-model");
    form.set("display_name", "Local model");
    form.set("base_url", "http://127.0.0.1:8001/v1");
    form.set("location", "local");
    form.set("context_window", "32768");
    form.set("tool_calling", "on");
    form.set("secret_provider", "local-secrets");
    form.set("secret_id", "provider-token");
    form.set("secret_scope", "platform");
    form.set("secret_version", "current");

    const input = buildConfigureModelInput(form);

    expect(input).toMatchObject({
      adapter_id: "adapter-test",
      provider_id: "provider-local",
      model_config_id: "model-local",
      provider_model: "native-model",
      location: "local",
      capabilities: {
        context_window: 32768,
        tool_calling: true,
        structured_output: false,
        streaming: false,
        modalities: ["text"],
      },
      credential_ref: {
        provider: "local-secrets",
        secret_id: "provider-token",
        scope: "platform",
        version: "current",
      },
    });
    expect(Object.keys(input.credential_ref ?? {})).not.toContain("value");
    expect(Object.keys(input.credential_ref ?? {})).not.toContain("token");
    expect(Object.keys(input.credential_ref ?? {})).not.toContain("password");
    expect(Object.keys(input.credential_ref ?? {})).not.toContain("api_key");
  });

  it("requires complete SecretReference metadata rather than accepting partial credential input", () => {
    const form = new FormData();
    form.set("adapter_id", "adapter-test");
    form.set("provider_id", "provider-local");
    form.set("model_config_id", "model-local");
    form.set("provider_model", "native-model");
    form.set("base_url", "http://127.0.0.1:8001/v1");
    form.set("location", "local");
    form.set("secret_id", "provider-token");

    expect(() => buildConfigureModelInput(form)).toThrow(/SecretReference requires/i);
  });

  it("derives General Assistant project binding from the selected canonical Workspace", () => {
    const current = status({
      state: "needs_general_assistant",
      candidate_project_ids: ["project-a", "project-b"],
      candidate_workspace_ids: ["workspace-a", "workspace-b"],
    });

    expect(generalAssistantCloneScope(workspace("workspace-b", "project-b"), current)).toEqual({
      project_id: "project-b",
      workspace_id: "workspace-b",
    });
  });

  it("rejects a Workspace whose canonical Project is outside onboarding candidates", () => {
    const current = status({
      state: "needs_general_assistant",
      candidate_project_ids: ["project-a"],
      candidate_workspace_ids: ["workspace-b"],
    });

    expect(() => generalAssistantCloneScope(workspace("workspace-b", "project-b"), current))
      .toThrow(/Workspace Project/i);
  });

  it("passes explicit execution-precise Project, Workspace and Agent IDs to the first Task", () => {
    const current = status({
      state: "needs_selection",
      selection_required: true,
      selection_kind: "project",
      candidate_project_ids: ["project-a", "project-b"],
      candidate_workspace_ids: ["workspace-a", "workspace-b"],
      candidate_agent_ids: ["agent-a", "agent-b"],
    });
    const form = new FormData();
    form.set("objective", "Return one short response.");
    form.set("project_id", "project-b");
    form.set("workspace_id", "workspace-b");
    form.set("agent_id", "agent-b");

    expect(buildFirstRunTaskInput(form, current)).toEqual({
      objective: "Return one short response.",
      project_id: "project-b",
      workspace_id: "workspace-b",
      agent_id: "agent-b",
    });
  });

  it("uses explicit Agent identity to avoid inventing mixed Project/Workspace path combinations", () => {
    const current = status({
      state: "needs_selection",
      selection_required: true,
      selection_kind: "project",
      candidate_project_ids: ["project-a", "project-b"],
      candidate_workspace_ids: ["workspace-a", "workspace-b"],
      candidate_agent_ids: ["agent-a", "agent-b"],
    });
    const form = new FormData();
    form.set("objective", "Return one short response.");
    form.set("agent_id", "agent-b");

    expect(buildFirstRunTaskInput(form, current)).toEqual({
      objective: "Return one short response.",
      agent_id: "agent-b",
    });
  });

  it("does not guess among multiple executable Agent candidates", () => {
    const current = status({
      state: "needs_selection",
      selection_required: true,
      selection_kind: "agent",
      candidate_project_ids: ["project-a"],
      candidate_workspace_ids: ["workspace-a"],
      candidate_agent_ids: ["agent-a", "agent-b"],
    });
    const form = new FormData();
    form.set("objective", "Return one short response.");

    expect(() => buildFirstRunTaskInput(form, current)).toThrow(/explicit agent/i);
  });

  it("uses the unique canonical candidate path without inventing a second readiness model", () => {
    const current = status({
      state: "ready_for_task",
      candidate_project_ids: ["project-a"],
      candidate_workspace_ids: ["workspace-a"],
      candidate_agent_ids: ["agent-a"],
      project_count: 1,
      workspace_count: 1,
      general_assistant_count: 1,
      executable_general_assistant_count: 1,
    });
    const form = new FormData();
    form.set("objective", "Return one short response.");

    expect(buildFirstRunTaskInput(form, current)).toMatchObject({
      project_id: "project-a",
      workspace_id: "workspace-a",
      agent_id: "agent-a",
    });
  });

  it("renders the returned canonical Task, Run and Result with normal detail links", () => {
    const html = renderToStaticMarkup(
      <FirstResult
        result={{
          id: "result-1",
          type: "first_run_result",
          task_id: "task-1",
          task_status: "succeeded",
          run_id: "run-1",
          run_status: "succeeded",
          agent_id: "agent-1",
          workspace_id: "workspace-1",
          project_id: "project-1",
          result_id: "result-1",
          output: { text: "Local response" },
        }}
      />,
    );

    expect(html).toContain("First canonical result");
    expect(html).toContain("/tasks/task-1");
    expect(html).toContain("/runs/run-1");
    expect(html).toContain("/results/result-1");
    expect(html).toContain("Local response");
  });

  it("represents restart revalidation as needs_model when configured models still exist", () => {
    const restarted = status({
      state: "needs_model",
      local_model_count: 1,
      text_capable_golden_path_model_count: 1,
      usable_golden_path_model_count: 0,
      guidance: ["Refresh canonical ModelProvider health before starting the first Task."],
    });

    expect(restarted.state).toBe("needs_model");
    expect(restarted.local_model_count).toBe(1);
    expect(restarted.usable_golden_path_model_count).toBe(0);
    expect(onboardingStatePresentation(restarted.state).title).toMatch(/model/i);
  });

  it("gates the product route on the canonical onboarding manifest resource", () => {
    expect(
      manifestResourceState(
        "ready",
        {
          api_version: "v1",
          resources: ["onboarding", "projects", "workspaces"],
          commands: ["onboarding.configure-model"],
          openapi: "/api/v1/openapi.json",
          live_updates: "/api/v1/events",
        },
        "onboarding",
      ),
    ).toBe("available");
    expect(
      manifestResourceState(
        "ready",
        {
          api_version: "v1",
          resources: ["projects", "workspaces"],
          commands: [],
          openapi: "/api/v1/openapi.json",
          live_updates: "/api/v1/events",
        },
        "onboarding",
      ),
    ).toBe("unavailable");
  });
});
