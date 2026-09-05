import { useCallback, useEffect, useState, type FormEvent } from "react";
import { BrowserSessionClient, type AuthenticatedActor } from "../api/browserSession";
import { ControlPlaneClient } from "../api/client";
import {
  OnboardingClient,
  type ConfigureOnboardingModelInput,
  type FirstRunTaskInput,
  type FirstRunTaskResult,
  type OnboardingState,
  type OnboardingStatus,
} from "../api/onboarding";
import type {
  APImanifest,
  CanonicalModel,
  CanonicalWorkspaceIdentity,
  JsonValue,
} from "../api/types";
import { AppLink } from "../app/router";
import {
  CanonicalId,
  Card,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const STATE_COPY: Record<OnboardingState, { title: string; detail: string }> = {
  needs_model: {
    title: "Connect a local or self-hosted model",
    detail:
      "First-run execution needs one routable text model. Remote or paid providers are never selected automatically.",
  },
  needs_project: {
    title: "Create a project",
    detail: "The model path is ready. Create a canonical Project owned by the authenticated user.",
  },
  needs_workspace: {
    title: "Create a workspace",
    detail:
      "Create a canonical Workspace inside the Project that will contain the first Assistant task.",
  },
  needs_general_assistant: {
    title: "Create the editable General Assistant",
    detail:
      "Bootstrap the standard catalog when needed, then clone the General Assistant into the selected Project and Workspace.",
  },
  needs_selection: {
    title: "Choose the execution path",
    detail:
      "More than one executable canonical path exists. Select the General Assistant whose canonical Project/Workspace binding should run the first task.",
  },
  ready_for_task: {
    title: "Run the first task",
    detail:
      "Exactly one executable first-run path is ready. Start a canonical Task and inspect its Run and Result.",
  },
};

interface OnboardingPageProps {
  client: ControlPlaneClient;
  onboarding: OnboardingClient;
  session: BrowserSessionClient;
  manifest: APImanifest | null;
}

export function OnboardingPage({ client, onboarding, session, manifest }: OnboardingPageProps) {
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [actor, setActor] = useState<AuthenticatedActor | null>(null);
  const [models, setModels] = useState<CanonicalModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [firstResult, setFirstResult] = useState<FirstRunTaskResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextStatus, nextActor] = await Promise.all([onboarding.status(), session.me()]);
      setStatus(nextStatus);
      setActor(nextActor);
      setLoadError(null);
      if (manifest?.resources.includes("models")) {
        try {
          const modelPage = await client.listModels({ limit: 100 });
          setModels(modelPage.items);
        } catch {
          setModels([]);
        }
      } else {
        setModels([]);
      }
    } catch (error) {
      setLoadError(error);
    } finally {
      setLoading(false);
    }
  }, [client, manifest, onboarding, session]);

  useEffect(() => {
    void load();
  }, [load]);

  const perform = useCallback(
    async (label: string, operation: () => Promise<void>, success: string) => {
      setBusy(label);
      setActionError(null);
      setNotice(null);
      try {
        await operation();
        setNotice(success);
        await load();
      } catch (error) {
        setActionError(error);
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  if (loading && status === null) return <LoadingState label="Loading first-run state…" />;
  if (loadError && status === null) {
    return <ErrorState error={loadError} onRetry={() => void load()} />;
  }
  if (status === null) return <LoadingState label="Loading first-run state…" />;

  const providerIds = Array.from(
    new Set(
      models
        .filter((model) => model.location === "local" || model.location === "self_hosted")
        .map((model) => model.provider_id),
    ),
  ).sort();

  const configureAvailable = commandAvailable(manifest, "onboarding.configure-model");
  const bootstrapAvailable = commandAvailable(manifest, "standard-agent.bootstrap");
  const cloneAvailable = commandAvailable(manifest, "standard-agent.clone");
  const firstTaskAvailable = commandAvailable(manifest, "onboarding.run-first-task");
  const projectAvailable = manifest?.resources.includes("projects") ?? false;
  const workspaceAvailable = manifest?.resources.includes("workspaces") ?? false;
  const providerAdminAvailable = manifest?.resources.includes("model-providers") ?? false;

  const configureModel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await perform(
      "configure-model",
      async () => {
        await onboarding.configureModel(buildConfigureModelInput(form));
      },
      "Model configuration validated and saved. First-run status was refreshed.",
    );
  };

  const createProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (actor === null) return;
    const form = new FormData(event.currentTarget);
    await perform(
      "create-project",
      async () => {
        await client.createProject({
          name: requiredText(form, "name"),
          owner_type: "user",
          owner_id: actor.actor_id,
        });
      },
      "Project created. First-run status was refreshed.",
    );
  };

  const createWorkspace = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await perform(
      "create-workspace",
      async () => {
        await client.createWorkspace({
          project_id: requiredText(form, "project_id"),
          workspace_type: "persistent_project",
          access_mode: "read_write",
          retention: "persistent",
        });
      },
      "Workspace created. First-run status was refreshed.",
    );
  };

  const bootstrap = async () => {
    await perform(
      "bootstrap-agents",
      async () => {
        await onboarding.bootstrapStandardAgents();
      },
      "Standard Agent catalog bootstrapped. You can now create the editable General Assistant.",
    );
  };

  const cloneGeneralAssistant = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await perform(
      "clone-agent",
      async () => {
        const workspace = await client.getWorkspace(requiredText(form, "workspace_id"));
        const scope = generalAssistantCloneScope(workspace, status);
        const name = optionalText(form, "name");
        await onboarding.cloneGeneralAssistant({
          ...scope,
          ...(name ? { name } : {}),
        });
      },
      "Editable General Assistant created. First-run status was refreshed.",
    );
  };

  const refreshProvider = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await perform(
      "refresh-provider",
      async () => {
        await client.refreshModelProviderHealth(requiredText(form, "provider_id"));
      },
      "Provider health revalidated through the canonical ModelProvider API.",
    );
  };

  const runFirstTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy("run-first-task");
    setActionError(null);
    setNotice(null);
    try {
      const result = await onboarding.runFirstTask(buildFirstRunTaskInput(form, status));
      setFirstResult(result);
      setNotice("The first canonical Task completed and produced a Result.");
      await load();
    } catch (error) {
      setActionError(error);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">First run</p>
        <h1>Guided onboarding</h1>
        <p>
          This journey uses the same canonical Control Plane resources and commands as the CLI.
          It never contacts a model backend directly and never selects a remote or paid provider
          implicitly.
        </p>
      </header>

      {actionError ? <ErrorState error={actionError} /> : null}
      {loadError ? <ErrorState error={loadError} onRetry={() => void load()} /> : null}
      {notice ? <div className="state" role="status"><strong>{notice}</strong></div> : null}

      <Card title="Current first-run state">
        <div className="detail-header">
          <OnboardingStateSummary status={status} />
          <button className="secondary" disabled={loading} onClick={() => void load()}>
            {loading ? "Refreshing…" : "Refresh status"}
          </button>
        </div>
        <Guidance status={status} />
      </Card>

      <div className="metrics">
        <Metric
          label="Usable local/self-hosted models"
          value={status.usable_golden_path_model_count}
        />
        <Metric label="Projects" value={status.project_count} />
        <Metric label="Workspaces" value={status.workspace_count} />
        <Metric label="Executable assistants" value={status.executable_general_assistant_count} />
      </div>

      {status.state === "needs_model" ? (
        <>
          <Card title="Model setup">
            {!configureAvailable ? (
              <UnavailableAction text="The onboarding.configure-model command is not advertised by this deployment." />
            ) : status.installed_model_adapter_ids.length === 0 ? (
              <UnavailableAction text="No onboarding ModelProvider adapter is installed in this deployment." />
            ) : (
              <ModelSetupForm
                adapterIds={status.installed_model_adapter_ids}
                busy={busy === "configure-model"}
                onSubmit={configureModel}
              />
            )}
          </Card>

          {status.local_model_count + status.self_hosted_model_count > 0 ? (
            <Card title="Revalidate an existing provider">
              <p>
                A configured local/self-hosted model exists but is not currently routable. After
                a restart, runtime provider health intentionally starts unknown until this canonical
                health check succeeds.
              </p>
              {!providerAdminAvailable ? (
                <UnavailableAction text="ModelProvider administration is unavailable in the current API manifest." />
              ) : providerIds.length === 0 ? (
                <UnavailableAction text="No local/self-hosted provider ID could be read from the canonical model inventory." />
              ) : (
                <form className="form-grid" onSubmit={refreshProvider}>
                  <label>
                    Provider
                    <select name="provider_id" defaultValue={providerIds[0]} required>
                      {providerIds.map((providerId) => (
                        <option key={providerId} value={providerId}>{providerId}</option>
                      ))}
                    </select>
                  </label>
                  <button className="primary" disabled={busy === "refresh-provider"}>
                    {busy === "refresh-provider" ? "Revalidating…" : "Revalidate provider health"}
                  </button>
                </form>
              )}
              {models.length ? <ModelHealthTable models={models} /> : null}
            </Card>
          ) : null}
        </>
      ) : null}

      {status.state === "needs_project" ? (
        <Card title="Create project">
          {!projectAvailable ? (
            <UnavailableAction text="The canonical Project resource is unavailable in this deployment." />
          ) : actor === null ? (
            <UnavailableAction text="An authenticated browser user is required to create the Project." />
          ) : (
            <form className="form-grid" onSubmit={createProject}>
              <label>
                Project name
                <input name="name" required placeholder="My first project" />
              </label>
              <div className="context-summary">
                <span>Owner</span>
                <strong>user:{actor.actor_id}</strong>
              </div>
              <button className="primary" disabled={busy === "create-project"}>
                {busy === "create-project" ? "Creating…" : "Create project"}
              </button>
            </form>
          )}
        </Card>
      ) : null}

      {status.state === "needs_workspace" ? (
        <Card title="Create workspace">
          {!workspaceAvailable ? (
            <UnavailableAction text="The canonical Workspace resource is unavailable in this deployment." />
          ) : status.candidate_project_ids.length === 0 ? (
            <UnavailableAction text="No owned Project candidate was returned by onboarding." />
          ) : (
            <form className="form-grid" onSubmit={createWorkspace}>
              <CanonicalSelect
                label="Project"
                name="project_id"
                values={status.candidate_project_ids}
              />
              <div className="context-summary">
                <span>Workspace profile</span>
                <strong>persistent project · read/write</strong>
              </div>
              <button className="primary" disabled={busy === "create-workspace"}>
                {busy === "create-workspace" ? "Creating…" : "Create workspace"}
              </button>
            </form>
          )}
        </Card>
      ) : null}

      {status.state === "needs_general_assistant" ? (
        <Card title="General Assistant">
          {status.general_assistant_blockers.length ? (
            <Blockers blockers={status.general_assistant_blockers} />
          ) : null}
          {!status.starter_catalog_installed ? (
            <div className="state state-warning">
              <strong>The standard Agent catalog is not installed yet.</strong>
              <p>
                Bootstrap installs the bundled canonical definitions; it does not create a
                user-owned Assistant until you clone it.
              </p>
              <button
                className="primary"
                disabled={!bootstrapAvailable || busy === "bootstrap-agents"}
                onClick={() => void bootstrap()}
              >
                {busy === "bootstrap-agents" ? "Bootstrapping…" : "Bootstrap standard Agents"}
              </button>
              {!bootstrapAvailable ? <p>The standard-agent.bootstrap command is unavailable.</p> : null}
            </div>
          ) : (
            <GeneralAssistantCloneForm
              workspaceIds={status.candidate_workspace_ids}
              busy={busy === "clone-agent"}
              available={cloneAvailable}
              onSubmit={cloneGeneralAssistant}
            />
          )}
          <p>
            Existing editable Agents remain visible under <AppLink href="/agents">Agents</AppLink>.
          </p>
        </Card>
      ) : null}

      {status.state === "needs_selection" ? (
        <Card title="Select an executable path and run the first task">
          {!firstTaskAvailable ? (
            <UnavailableAction text="The onboarding.run-first-task command is unavailable in this deployment." />
          ) : (
            <FirstTaskForm
              status={status}
              busy={busy === "run-first-task"}
              onSubmit={runFirstTask}
            />
          )}
        </Card>
      ) : null}

      {status.state === "ready_for_task" ? (
        <Card title="Run the first task">
          {!firstTaskAvailable ? (
            <UnavailableAction text="The onboarding.run-first-task command is unavailable in this deployment." />
          ) : (
            <FirstTaskForm
              status={status}
              busy={busy === "run-first-task"}
              onSubmit={runFirstTask}
            />
          )}
        </Card>
      ) : null}

      {firstResult ? <FirstResult result={firstResult} /> : null}

      <Card title="Safety and provider policy">
        <ul>
          <li>Local and self-hosted model configurations are distinct from remote configurations.</li>
          <li>
            Remote/paid provider auto-selection:{" "}
            <strong>{String(status.automatic_paid_provider_selection)}</strong>.
          </li>
          <li>
            Secret values are never entered here; credential-bearing endpoints use only canonical
            SecretReference metadata.
          </li>
          <li>
            All mutations pass through BrowserSession CSRF handling and Control Plane idempotency
            keys.
          </li>
        </ul>
      </Card>
    </div>
  );
}

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
  const input: FirstRunTaskInput = {
    objective: requiredText(form, "objective"),
  };
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

export function OnboardingStateSummary({ status }: { status: OnboardingStatus }) {
  const stateCopy = STATE_COPY[status.state];
  return (
    <div>
      <StatusBadge value={status.state} />
      <h3>{stateCopy.title}</h3>
      <p>{stateCopy.detail}</p>
    </div>
  );
}

function ModelSetupForm({
  adapterIds,
  busy,
  onSubmit,
}: {
  adapterIds: string[];
  busy: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <form className="stack" onSubmit={onSubmit}>
      <div className="form-grid">
        <label>
          Installed adapter
          <select name="adapter_id" defaultValue={adapterIds[0]} required>
            {adapterIds.map((adapterId) => (
              <option value={adapterId} key={adapterId}>{adapterId}</option>
            ))}
          </select>
        </label>
        <label>
          Location
          <select name="location" defaultValue="local" required>
            <option value="local">local — loopback endpoint on this device</option>
            <option value="self_hosted">self_hosted — explicitly managed endpoint</option>
          </select>
        </label>
        <label>Provider ID<input name="provider_id" required placeholder="local-provider" /></label>
        <label>Model configuration ID<input name="model_config_id" required placeholder="model-local" /></label>
        <label>Provider-native model name<input name="provider_model" required /></label>
        <label>Display name<input name="display_name" /></label>
        <label>Base URL<input name="base_url" required placeholder="http://127.0.0.1:PORT/..." /></label>
        <label>Context window<input name="context_window" inputMode="numeric" /></label>
      </div>
      <div className="actions">
        <label><input type="checkbox" name="tool_calling" /> Tool calling</label>
        <label><input type="checkbox" name="structured_output" /> Structured output</label>
        <label><input type="checkbox" name="streaming" /> Streaming</label>
      </div>
      <fieldset className="card">
        <legend>Optional SecretReference metadata</legend>
        <p>
          Enter only the canonical reference that identifies an already provisioned secret. Do not
          enter an API key, token or password value.
        </p>
        <div className="form-grid">
          <label>Secret provider<input name="secret_provider" placeholder="local-secrets" /></label>
          <label>Secret ID<input name="secret_id" /></label>
          <label>Scope<input name="secret_scope" placeholder="platform" /></label>
          <label>Version<input name="secret_version" /></label>
        </div>
      </fieldset>
      <button className="primary" disabled={busy}>
        {busy ? "Validating…" : "Validate and save model"}
      </button>
    </form>
  );
}

function GeneralAssistantCloneForm({
  workspaceIds,
  busy,
  available,
  onSubmit,
}: {
  workspaceIds: string[];
  busy: boolean;
  available: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  if (!available) {
    return <UnavailableAction text="The standard-agent.clone command is unavailable in this deployment." />;
  }
  if (!workspaceIds.length) {
    return <UnavailableAction text="Onboarding did not return a Workspace scope for the General Assistant clone." />;
  }
  return (
    <form className="form-grid" onSubmit={onSubmit}>
      <CanonicalSelect label="Workspace" name="workspace_id" values={workspaceIds} />
      <div className="context-summary">
        <span>Project binding</span>
        <strong>derived from the canonical Workspace</strong>
      </div>
      <label>Name (optional)<input name="name" placeholder="General Assistant" /></label>
      <button className="primary" disabled={busy}>
        {busy ? "Creating…" : "Create editable General Assistant"}
      </button>
    </form>
  );
}

function FirstTaskForm({
  status,
  busy,
  onSubmit,
}: {
  status: OnboardingStatus;
  busy: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <form className="stack" onSubmit={onSubmit}>
      {status.state === "needs_selection" ? (
        <div className="form-grid">
          <SelectionSelect
            label="Executable General Assistant"
            name="agent_id"
            values={status.candidate_agent_ids}
          />
          <div className="context-summary">
            <span>Scope binding</span>
            <strong>resolved from the selected canonical Agent</strong>
          </div>
        </div>
      ) : null}
      <div className="form-grid">
        <label>Task title (optional)<input name="title" placeholder="First General Assistant Task" /></label>
        <label>
          Objective
          <input name="objective" required defaultValue="Return one short local response." />
        </label>
      </div>
      <button className="primary" disabled={busy}>
        {busy ? "Running…" : "Run first canonical Task"}
      </button>
    </form>
  );
}

function SelectionSelect({ label, name, values }: { label: string; name: string; values: string[] }) {
  if (!values.length) return null;
  return (
    <label>
      {label}
      <select
        name={name}
        defaultValue={values.length === 1 ? values[0] : ""}
        required={values.length > 1}
      >
        {values.length > 1 ? <option value="">Select…</option> : null}
        {values.map((value) => <option key={value} value={value}>{value}</option>)}
      </select>
    </label>
  );
}

function CanonicalSelect({ label, name, values }: { label: string; name: string; values: string[] }) {
  return (
    <label>
      {label}
      <select name={name} defaultValue={values[0]} required>
        {values.map((value) => <option key={value} value={value}>{value}</option>)}
      </select>
    </label>
  );
}

function Guidance({ status }: { status: OnboardingStatus }) {
  const guidance = status.guidance.filter((item): item is string => typeof item === "string");
  if (!guidance.length) return null;
  return (
    <ul>
      {guidance.map((item) => <li key={item}>{item}</li>)}
    </ul>
  );
}

function Blockers({ blockers }: { blockers: Array<Record<string, JsonValue>> }) {
  return (
    <div className="state state-warning">
      <strong>Existing General Assistant configuration is not executable yet.</strong>
      {blockers.map((blocker, index) => (
        <div key={`${String(blocker.agent_id ?? "agent")}-${index}`}>
          <p>
            {typeof blocker.message === "string"
              ? blocker.message
              : "Execution preflight failed."}
          </p>
          <small>
            {typeof blocker.code === "string"
              ? `Code ${blocker.code}`
              : "Canonical preflight blocker"}
            {typeof blocker.agent_id === "string" ? ` · Agent ${blocker.agent_id}` : ""}
          </small>
        </div>
      ))}
    </div>
  );
}

function ModelHealthTable({ models }: { models: CanonicalModel[] }) {
  const local = models.filter(
    (model) => model.location === "local" || model.location === "self_hosted",
  );
  if (!local.length) return null;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Model</th><th>Location</th><th>Provider</th><th>Effective health</th></tr></thead>
        <tbody>
          {local.map((model) => (
            <tr key={model.id}>
              <td>{model.display_name}<div><CanonicalId value={model.id} /></div></td>
              <td><StatusBadge value={model.location} /></td>
              <td><CanonicalId value={model.provider_id} /></td>
              <td><StatusBadge value={model.effective_health} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FirstResult({ result }: { result: FirstRunTaskResult }) {
  return (
    <Card title="First canonical result">
      <div className="metrics">
        <Metric label="Task" value={result.task_status} />
        <Metric label="Run" value={result.run_status} />
        <Metric label="Agent" value={result.agent_id} />
        <Metric label="Result" value={result.result_id} />
      </div>
      <div className="actions">
        <AppLink href={`/tasks/${result.task_id}`}>Open Task</AppLink>
        <AppLink href={`/runs/${result.run_id}`}>Open Run</AppLink>
        <AppLink href={`/results/${result.result_id}`}>Open Result</AppLink>
      </div>
      <pre>{JSON.stringify(result.output, null, 2)}</pre>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function UnavailableAction({ text }: { text: string }) {
  return <div className="state state-warning"><strong>Unavailable</strong><p>{text}</p></div>;
}

function commandAvailable(manifest: APImanifest | null, command: string): boolean {
  if (manifest === null || manifest.commands === undefined) return true;
  return manifest.commands.includes(command);
}

function requiredText(form: FormData, field: string): string {
  const value = String(form.get(field) ?? "").trim();
  if (!value) throw new Error(`${field} is required.`);
  return value;
}

function optionalText(form: FormData, field: string): string | undefined {
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
