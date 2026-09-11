import { describe, expect, it } from "vitest";
import type { OnboardingStatus } from "../../api/onboarding";
import type { APImanifest } from "../../api/types";
import { buildFirstRunTaskInput, commandAvailable } from "./state";

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

const manifest: APImanifest = {
  api_version: "v1",
  resources: ["onboarding"],
  commands: ["onboarding.first-task"],
  openapi: "/api/v1/openapi.json",
  live_updates: "/api/v1/events",
};

describe("onboarding interaction state", () => {
  it("uses the canonical manifest when command discovery is available", () => {
    expect(commandAvailable(null, "onboarding.first-task")).toBe(true);
    expect(commandAvailable(manifest, "onboarding.first-task")).toBe(true);
    expect(commandAvailable(manifest, "onboarding.configure-model")).toBe(false);
  });

  it("rejects explicit first-run selections outside canonical onboarding candidates", () => {
    const current = status({
      state: "needs_selection",
      selection_required: true,
      selection_kind: "project",
      candidate_project_ids: ["project-a"],
      candidate_workspace_ids: ["workspace-a"],
      candidate_agent_ids: ["agent-a"],
    });
    const form = new FormData();
    form.set("objective", "Run the first task.");
    form.set("project_id", "project-outside-candidates");

    expect(() => buildFirstRunTaskInput(form, current)).toThrow(/project_id must be one of/i);
  });
});
