import type { FormEvent } from "react";
import type { OnboardingStatus } from "../../api/onboarding";
import { UnavailableAction } from "./presentation";

export function ModelSetupForm({
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
        <label>Installed adapter<select name="adapter_id" defaultValue={adapterIds[0]} required>{adapterIds.map((adapterId) => <option value={adapterId} key={adapterId}>{adapterId}</option>)}</select></label>
        <label>Location<select name="location" defaultValue="local" required><option value="local">local — loopback endpoint on this device</option><option value="self_hosted">self_hosted — explicitly managed endpoint</option></select></label>
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
        <p>Enter only the canonical reference that identifies an already provisioned secret. Do not enter an API key, token or password value.</p>
        <div className="form-grid">
          <label>Secret provider<input name="secret_provider" placeholder="local-secrets" /></label>
          <label>Secret ID<input name="secret_id" /></label>
          <label>Scope<input name="secret_scope" placeholder="platform" /></label>
          <label>Version<input name="secret_version" /></label>
        </div>
      </fieldset>
      <button className="primary" disabled={busy}>{busy ? "Validating…" : "Validate and save model"}</button>
    </form>
  );
}

export function GeneralAssistantCloneForm({
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
  if (!available) return <UnavailableAction text="The standard-agent.clone command is unavailable in this deployment." />;
  if (!workspaceIds.length) return <UnavailableAction text="Onboarding did not return a Workspace scope for the General Assistant clone." />;
  return (
    <form className="form-grid" onSubmit={onSubmit}>
      <CanonicalSelect label="Workspace" name="workspace_id" values={workspaceIds} />
      <div className="context-summary"><span>Project binding</span><strong>derived from the canonical Workspace</strong></div>
      <label>Name (optional)<input name="name" placeholder="General Assistant" /></label>
      <button className="primary" disabled={busy}>{busy ? "Creating…" : "Create editable General Assistant"}</button>
    </form>
  );
}

export function FirstTaskForm({
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
          <SelectionSelect label="Executable General Assistant" name="agent_id" values={status.candidate_agent_ids} />
          <div className="context-summary"><span>Scope binding</span><strong>resolved from the selected canonical Agent</strong></div>
        </div>
      ) : null}
      <div className="form-grid">
        <label>Task title (optional)<input name="title" placeholder="First General Assistant Task" /></label>
        <label>Objective<input name="objective" required defaultValue="Return one short local response." /></label>
      </div>
      <button className="primary" disabled={busy}>{busy ? "Running…" : "Run first canonical Task"}</button>
    </form>
  );
}

export function CanonicalSelect({ label, name, values }: { label: string; name: string; values: string[] }) {
  return <label>{label}<select name={name} defaultValue={values[0]} required>{values.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>;
}

function SelectionSelect({ label, name, values }: { label: string; name: string; values: string[] }) {
  if (!values.length) return null;
  return (
    <label>{label}<select name={name} defaultValue={values.length === 1 ? values[0] : ""} required={values.length > 1}>{values.length > 1 ? <option value="">Select…</option> : null}{values.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
  );
}
