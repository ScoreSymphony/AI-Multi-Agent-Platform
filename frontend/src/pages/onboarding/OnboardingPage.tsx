import { useCallback, useEffect, useState, type FormEvent } from "react";
import { BrowserSessionClient, type AuthenticatedActor } from "../../api/browserSession";
import { ControlPlaneClient } from "../../api/client";
import {
  OnboardingClient,
  type FirstRunTaskResult,
  type OnboardingStatus,
} from "../../api/onboarding";
import type { APImanifest, CanonicalModel } from "../../api/types";
import { Card, ErrorState, LoadingState } from "../../components/States";
import { FirstResult, Guidance, Metric, OnboardingStateSummary } from "./presentation";
import {
  buildConfigureModelInput,
  buildFirstRunTaskInput,
  commandAvailable,
  generalAssistantCloneScope,
  optionalText,
  requiredText,
} from "./state";
import { OnboardingSteps } from "./steps";

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

  useEffect(() => { void load(); }, [load]);

  const perform = useCallback(async (label: string, operation: () => Promise<void>, success: string) => {
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
  }, [load]);

  if (loading && status === null) return <LoadingState label="Loading first-run state…" />;
  if (loadError && status === null) return <ErrorState error={loadError} onRetry={() => void load()} />;
  if (status === null) return <LoadingState label="Loading first-run state…" />;

  const providerIds = Array.from(new Set(models.filter((model) => model.location === "local" || model.location === "self_hosted").map((model) => model.provider_id))).sort();

  const configureModel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await perform("configure-model", async () => { await onboarding.configureModel(buildConfigureModelInput(form)); }, "Model configuration validated and saved. First-run status was refreshed.");
  };

  const createProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (actor === null) return;
    const form = new FormData(event.currentTarget);
    await perform("create-project", async () => { await client.createProject({ name: requiredText(form, "name"), owner_type: "user", owner_id: actor.actor_id }); }, "Project created. First-run status was refreshed.");
  };

  const createWorkspace = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await perform("create-workspace", async () => { await client.createWorkspace({ project_id: requiredText(form, "project_id"), workspace_type: "persistent_project", access_mode: "read_write", retention: "persistent" }); }, "Workspace created. First-run status was refreshed.");
  };

  const bootstrap = async () => {
    await perform("bootstrap-agents", async () => { await onboarding.bootstrapStandardAgents(); }, "Standard Agent catalog bootstrapped. You can now create the editable General Assistant.");
  };

  const cloneGeneralAssistant = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await perform("clone-agent", async () => {
      const workspace = await client.getWorkspace(requiredText(form, "workspace_id"));
      const scope = generalAssistantCloneScope(workspace, status);
      const name = optionalText(form, "name");
      await onboarding.cloneGeneralAssistant({ ...scope, ...(name ? { name } : {}) });
    }, "Editable General Assistant created. First-run status was refreshed.");
  };

  const refreshProvider = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await perform("refresh-provider", async () => { await client.refreshModelProviderHealth(requiredText(form, "provider_id")); }, "Provider health revalidated through the canonical ModelProvider API.");
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
        <p className="eyebrow">First run</p><h1>Guided onboarding</h1>
        <p>This journey uses the same canonical Control Plane resources and commands as the CLI. It never contacts a model backend directly and never selects a remote or paid provider implicitly.</p>
      </header>
      {actionError ? <ErrorState error={actionError} /> : null}
      {loadError ? <ErrorState error={loadError} onRetry={() => void load()} /> : null}
      {notice ? <div className="state" role="status"><strong>{notice}</strong></div> : null}

      <Card title="Current first-run state">
        <div className="detail-header"><OnboardingStateSummary status={status} /><button className="secondary" disabled={loading} onClick={() => void load()}>{loading ? "Refreshing…" : "Refresh status"}</button></div>
        <Guidance status={status} />
      </Card>

      <div className="metrics">
        <Metric label="Usable local/self-hosted models" value={status.usable_golden_path_model_count} />
        <Metric label="Projects" value={status.project_count} />
        <Metric label="Workspaces" value={status.workspace_count} />
        <Metric label="Executable assistants" value={status.executable_general_assistant_count} />
      </div>

      <OnboardingSteps
        status={status}
        actor={actor}
        models={models}
        providerIds={providerIds}
        busy={busy}
        configureAvailable={commandAvailable(manifest, "onboarding.configure-model")}
        bootstrapAvailable={commandAvailable(manifest, "standard-agent.bootstrap")}
        cloneAvailable={commandAvailable(manifest, "standard-agent.clone")}
        firstTaskAvailable={commandAvailable(manifest, "onboarding.run-first-task")}
        projectAvailable={manifest?.resources.includes("projects") ?? false}
        workspaceAvailable={manifest?.resources.includes("workspaces") ?? false}
        providerAdminAvailable={manifest?.resources.includes("model-providers") ?? false}
        onConfigureModel={(event) => void configureModel(event)}
        onRefreshProvider={(event) => void refreshProvider(event)}
        onCreateProject={(event) => void createProject(event)}
        onCreateWorkspace={(event) => void createWorkspace(event)}
        onBootstrap={() => void bootstrap()}
        onCloneGeneralAssistant={(event) => void cloneGeneralAssistant(event)}
        onRunFirstTask={(event) => void runFirstTask(event)}
      />

      {firstResult ? <FirstResult result={firstResult} /> : null}

      <Card title="Safety and provider policy">
        <ul>
          <li>Local and self-hosted model configurations are distinct from remote configurations.</li>
          <li>Remote/paid provider auto-selection: <strong>{String(status.automatic_paid_provider_selection)}</strong>.</li>
          <li>Secret values are never entered here; credential-bearing endpoints use only canonical SecretReference metadata.</li>
          <li>All mutations pass through BrowserSession CSRF handling and Control Plane idempotency keys.</li>
        </ul>
      </Card>
    </div>
  );
}
