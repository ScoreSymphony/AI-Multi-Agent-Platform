import type { FormEvent } from "react";
import type { AuthenticatedActor } from "../../api/browserSession";
import type { OnboardingStatus } from "../../api/onboarding";
import type { CanonicalModel } from "../../api/types";
import { AppLink } from "../../app/router";
import { Card } from "../../components/States";
import { CanonicalSelect, FirstTaskForm, GeneralAssistantCloneForm, ModelSetupForm } from "./forms";
import { Blockers, ModelHealthTable, UnavailableAction } from "./presentation";

interface OnboardingStepsProps {
  status: OnboardingStatus;
  actor: AuthenticatedActor | null;
  models: CanonicalModel[];
  providerIds: string[];
  busy: string | null;
  configureAvailable: boolean;
  bootstrapAvailable: boolean;
  cloneAvailable: boolean;
  firstTaskAvailable: boolean;
  projectAvailable: boolean;
  workspaceAvailable: boolean;
  providerAdminAvailable: boolean;
  onConfigureModel: (event: FormEvent<HTMLFormElement>) => void;
  onRefreshProvider: (event: FormEvent<HTMLFormElement>) => void;
  onCreateProject: (event: FormEvent<HTMLFormElement>) => void;
  onCreateWorkspace: (event: FormEvent<HTMLFormElement>) => void;
  onBootstrap: () => void;
  onCloneGeneralAssistant: (event: FormEvent<HTMLFormElement>) => void;
  onRunFirstTask: (event: FormEvent<HTMLFormElement>) => void;
}

export function OnboardingSteps(props: OnboardingStepsProps) {
  const { status, busy } = props;
  return (
    <>
      {status.state === "needs_model" ? <ModelStep {...props} /> : null}
      {status.state === "needs_project" ? <ProjectStep {...props} /> : null}
      {status.state === "needs_workspace" ? <WorkspaceStep {...props} /> : null}
      {status.state === "needs_general_assistant" ? <GeneralAssistantStep {...props} /> : null}
      {status.state === "needs_selection" ? (
        <Card title="Select an executable path and run the first task">
          {!props.firstTaskAvailable ? <UnavailableAction text="The onboarding.run-first-task command is unavailable in this deployment." /> : <FirstTaskForm status={status} busy={busy === "run-first-task"} onSubmit={props.onRunFirstTask} />}
        </Card>
      ) : null}
      {status.state === "ready_for_task" ? (
        <Card title="Run the first task">
          {!props.firstTaskAvailable ? <UnavailableAction text="The onboarding.run-first-task command is unavailable in this deployment." /> : <FirstTaskForm status={status} busy={busy === "run-first-task"} onSubmit={props.onRunFirstTask} />}
        </Card>
      ) : null}
    </>
  );
}

function ModelStep(props: OnboardingStepsProps) {
  const { status, models, providerIds, busy } = props;
  return (
    <>
      <Card title="Model setup">
        {!props.configureAvailable ? <UnavailableAction text="The onboarding.configure-model command is not advertised by this deployment." /> : status.installed_model_adapter_ids.length === 0 ? <UnavailableAction text="No onboarding ModelProvider adapter is installed in this deployment." /> : <ModelSetupForm adapterIds={status.installed_model_adapter_ids} busy={busy === "configure-model"} onSubmit={props.onConfigureModel} />}
      </Card>
      {status.local_model_count + status.self_hosted_model_count > 0 ? (
        <Card title="Revalidate an existing provider">
          <p>A configured local/self-hosted model exists but is not currently routable. After a restart, runtime provider health intentionally starts unknown until this canonical health check succeeds.</p>
          {!props.providerAdminAvailable ? <UnavailableAction text="ModelProvider administration is unavailable in the current API manifest." /> : providerIds.length === 0 ? <UnavailableAction text="No local/self-hosted provider ID could be read from the canonical model inventory." /> : (
            <form className="form-grid" onSubmit={props.onRefreshProvider}>
              <label>Provider<select name="provider_id" defaultValue={providerIds[0]} required>{providerIds.map((providerId) => <option key={providerId} value={providerId}>{providerId}</option>)}</select></label>
              <button className="primary" disabled={busy === "refresh-provider"}>{busy === "refresh-provider" ? "Revalidating…" : "Revalidate provider health"}</button>
            </form>
          )}
          {models.length ? <ModelHealthTable models={models} /> : null}
        </Card>
      ) : null}
    </>
  );
}

function ProjectStep(props: OnboardingStepsProps) {
  return (
    <Card title="Create project">
      {!props.projectAvailable ? <UnavailableAction text="The canonical Project resource is unavailable in this deployment." /> : props.actor === null ? <UnavailableAction text="An authenticated browser user is required to create the Project." /> : (
        <form className="form-grid" onSubmit={props.onCreateProject}>
          <label>Project name<input name="name" required placeholder="My first project" /></label>
          <div className="context-summary"><span>Owner</span><strong>user:{props.actor.actor_id}</strong></div>
          <button className="primary" disabled={props.busy === "create-project"}>{props.busy === "create-project" ? "Creating…" : "Create project"}</button>
        </form>
      )}
    </Card>
  );
}

function WorkspaceStep(props: OnboardingStepsProps) {
  return (
    <Card title="Create workspace">
      {!props.workspaceAvailable ? <UnavailableAction text="The canonical Workspace resource is unavailable in this deployment." /> : props.status.candidate_project_ids.length === 0 ? <UnavailableAction text="No owned Project candidate was returned by onboarding." /> : (
        <form className="form-grid" onSubmit={props.onCreateWorkspace}>
          <CanonicalSelect label="Project" name="project_id" values={props.status.candidate_project_ids} />
          <div className="context-summary"><span>Workspace profile</span><strong>persistent project · read/write</strong></div>
          <button className="primary" disabled={props.busy === "create-workspace"}>{props.busy === "create-workspace" ? "Creating…" : "Create workspace"}</button>
        </form>
      )}
    </Card>
  );
}

function GeneralAssistantStep(props: OnboardingStepsProps) {
  const { status } = props;
  return (
    <Card title="General Assistant">
      {status.general_assistant_blockers.length ? <Blockers blockers={status.general_assistant_blockers} /> : null}
      {!status.starter_catalog_installed ? (
        <div className="state state-warning">
          <strong>The standard Agent catalog is not installed yet.</strong>
          <p>Bootstrap installs the bundled canonical definitions; it does not create a user-owned Assistant until you clone it.</p>
          <button className="primary" disabled={!props.bootstrapAvailable || props.busy === "bootstrap-agents"} onClick={props.onBootstrap}>{props.busy === "bootstrap-agents" ? "Bootstrapping…" : "Bootstrap standard Agents"}</button>
          {!props.bootstrapAvailable ? <p>The standard-agent.bootstrap command is unavailable.</p> : null}
        </div>
      ) : <GeneralAssistantCloneForm workspaceIds={status.candidate_workspace_ids} busy={props.busy === "clone-agent"} available={props.cloneAvailable} onSubmit={props.onCloneGeneralAssistant} />}
      <p>Existing editable Agents remain visible under <AppLink href="/agents">Agents</AppLink>.</p>
    </Card>
  );
}
