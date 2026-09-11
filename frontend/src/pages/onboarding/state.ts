import type {
  ConfigureOnboardingModelInput,
  FirstRunTaskInput,
  OnboardingState,
  OnboardingStatus,
} from "../../api/onboarding";
import type { APImanifest, CanonicalWorkspaceIdentity } from "../../api/types";

const STATE_COPY: Record<OnboardingState, { title: string; detail: string }> = {
  needs_model: {
    title: "Connect a local or self-hosted model",
    detail: "First-run execution needs one routable text model. Remote or paid providers are never selected automatically.",
  },
  needs_project: {
    title: "Create a project",
    detail: "The model path is ready. Create a canonical Project owned by the authenticated user.",
  },
  needs_workspace: {
    title: "Create a workspace",
    detail: "Create a canonical Workspace inside the Project that will contain the first Assistant task.",
  },
  needs_general_assistant: {
    title: "Create the editable General Assistant",
    detail: "Bootstrap the standard catalog when needed, then clone the General Assistant into the selected Project and Workspace.",
  },
  needs_selection: {
    title: "Choose the execution path",
    detail: "More than one executable canonical path exists. Select the General Assistant whose canonical Project/Workspace binding should run the first task.",
  },
  ready_for_task: {
    title: "Run the first task",
    detail: "Exactly one executable first-run path is ready. Start a canonical Task and inspect its Run and Result.",
  },
};

export function buildConfigureModelInput(form: FormData): ConfigureOnboardingModelInput {
  const contextWindow = optionalInteger(form, "context_window");
  const secretProvider = optionalText(form, "secret_provider");
  const secretId = optionalText(form, "secret_id");
  const secretScope = optionalText(form, "secret_scope");
  const secretVersion = optionalText(form, "secret_version");
  const hasSecretReference = Boolean(secretProvider || secretId || secretScope || secretVersion);
  if (hasSecretReference && (!secretProvider || !secretId || !secretScope)) {
    throw new Error("SecretReference requires provider, secret ID and scope.");
  }

  const capabilities: ConfigureOnboardingModelInput["capabilities"] = {
    tool_calling: form.get("tool_calling") === "on",
    structured_output: form.get("structured_output") === "on",
    streaming: form.get("streaming") === "on",
    modalities: ["text"],
    reasoning: [],
    ...(contextWindow === undefined ? {} : { context_window: contextWindow }),
  };

  return {
    adapter_id: requiredText(form, "adapter_id"),
    provider_id: requiredText(form, "provider_id"),
    model_config_id: requiredText(form, "model_config_id"),
    provider_model: requiredText(form, "provider_model"),
    display_name: optionalText(form, "display_name"),
    base_url: requiredText(form, "base_url"),
    location: requiredText(form, "location") as "local" | "self_hosted",
    capabilities,
    ...(hasSecretReference
      ? {
          credential_ref: {
            provider: secretProvider!,
            secret_id: secretId!,
            scope: secretScope!,
            ...(secretVersion ? { version: secretVersion } : {}),
          },
        }
      : {}),
  };
}

export function generalAssistantCloneScope(
  workspace: CanonicalWorkspaceIdentity,
  status: OnboardingStatus,
): { project_id: string; workspace_id: string } {
  if (!status.candidate_workspace_ids.includes(workspace.id)) {
    throw new Error("Workspace must be one of the canonical onboarding candidates.");
  }
  if (!status.candidate_project_ids.includes(workspace.project_id)) {
    throw new Error("Workspace Project must be one of the canonical onboarding candidates.");
  }
  return { project_id: workspace.project_id, workspace_id: workspace.id };
}

export function buildFirstRunTaskInput(form: FormData, status: OnboardingStatus): FirstRunTaskInput {
  const input: FirstRunTaskInput = { objective: requiredText(form, "objective") };
  const title = optionalText(form, "title");
  if (title) input.title = title;

  for (const [field, candidates] of [
    ["project_id", status.candidate_project_ids],
    ["workspace_id", status.candidate_workspace_ids],
  ] as const) {
    const selected = optionalText(form, field);
    if (selected) {
      if (!candidates.includes(selected)) {
        throw new Error(`${field} must be one of the executable onboarding candidates.`);
      }
      input[field] = selected;
    } else if (candidates.length === 1) {
      input[field] = candidates[0];
    }
  }

  const selectedAgent = optionalText(form, "agent_id");
  if (selectedAgent) {
    if (!status.candidate_agent_ids.includes(selectedAgent)) {
      throw new Error("agent_id must be one of the executable onboarding candidates.");
    }
    input.agent_id = selectedAgent;
  } else if (status.candidate_agent_ids.length === 1) {
    input.agent_id = status.candidate_agent_ids[0];
  } else if (status.state === "needs_selection" && status.candidate_agent_ids.length > 1) {
    throw new Error("Select an explicit agent before starting the first task.");
  }
  return input;
}

export function onboardingStatePresentation(state: OnboardingState) {
  return STATE_COPY[state];
}

export function commandAvailable(manifest: APImanifest | null, command: string): boolean {
  if (manifest === null || manifest.commands === undefined) return true;
  return manifest.commands.includes(command);
}

export function requiredText(form: FormData, field: string): string {
  const value = String(form.get(field) ?? "").trim();
  if (!value) throw new Error(`${field} is required.`);
  return value;
}

export function optionalText(form: FormData, field: string): string | undefined {
  const value = String(form.get(field) ?? "").trim();
  return value || undefined;
}

function optionalInteger(form: FormData, field: string): number | undefined {
  const value = optionalText(form, field);
  if (value === undefined) return undefined;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${field} must be a positive integer.`);
  }
  return parsed;
}
