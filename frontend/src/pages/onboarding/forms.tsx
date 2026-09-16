import type { FormEvent } from "react";
import type {
  ModelSetupContract,
  ModelSetupFieldContract,
  OnboardingStatus,
} from "../../api/onboarding";
import { UnavailableAction } from "./presentation";

export function ModelSetupForm({
  adapterIds,
  contract,
  busy,
  onSubmit,
}: {
  adapterIds: string[];
  contract?: ModelSetupContract;
  busy: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const fields = contract?.fields ?? legacyModelSetupFields(adapterIds);
  const ordinaryFields = fields.filter(
    (field) => !field.secret_reference && field.input_kind !== "boolean",
  );
  const booleanFields = fields.filter(
    (field) => !field.secret_reference && field.input_kind === "boolean",
  );
  const secretReferenceFields = fields.filter((field) => field.secret_reference);

  return (
    <form className="stack" onSubmit={onSubmit}>
      {contract ? (
        <p>
          Configuration fields are supplied by the Control Plane contract version {contract.version}.
        </p>
      ) : (
        <p>
          This Control Plane predates the setup-field projection; compatibility fields are shown
          using the legacy browser mapping.
        </p>
      )}
      <div className="form-grid">
        {ordinaryFields.map((field) => (
          <ModelSetupField field={field} key={field.path} />
        ))}
      </div>
      {booleanFields.length > 0 ? (
        <div className="actions">
          {booleanFields.map((field) => (
            <label key={field.path}>
              <input type="checkbox" name={formName(field.path)} /> {field.label}
            </label>
          ))}
        </div>
      ) : null}
      {secretReferenceFields.length > 0 ? (
        <fieldset className="card">
          <legend>Optional SecretReference metadata</legend>
          <p>
            Enter only the canonical reference that identifies an already provisioned secret. Do
            not enter an API key, token or password value.
          </p>
          <div className="form-grid">
            {secretReferenceFields.map((field) => (
              <ModelSetupField field={field} key={field.path} />
            ))}
          </div>
        </fieldset>
      ) : null}
      <button className="primary" disabled={busy}>
        {busy ? "Validating…" : "Validate and save model"}
      </button>
    </form>
  );
}

function ModelSetupField({ field }: { field: ModelSetupFieldContract }) {
  const name = formName(field.path);
  if (field.input_kind === "select") {
    return (
      <label>
        {field.label}
        <select name={name} defaultValue={field.options[0] ?? ""} required={field.required}>
          {field.options.map((option) => (
            <option key={option} value={option}>{option}</option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label>
      {field.label}
      <input
        name={name}
        inputMode={field.input_kind === "integer" ? "numeric" : undefined}
        placeholder={field.placeholder ?? undefined}
        required={field.required}
        type={field.input_kind === "url" ? "url" : "text"}
      />
    </label>
  );
}

function formName(path: string): string {
  return {
    "capabilities.context_window": "context_window",
    "capabilities.tool_calling": "tool_calling",
    "capabilities.structured_output": "structured_output",
    "capabilities.streaming": "streaming",
    "credential_ref.provider": "secret_provider",
    "credential_ref.secret_id": "secret_id",
    "credential_ref.scope": "secret_scope",
    "credential_ref.version": "secret_version",
  }[path] ?? path;
}

function legacyModelSetupFields(adapterIds: string[]): ModelSetupFieldContract[] {
  return [
    setupField("adapter_id", "Installed adapter", "select", true, adapterIds),
    setupField("location", "Location", "select", true, ["local", "self_hosted"]),
    setupField("provider_id", "Provider ID", "text", true),
    setupField("model_config_id", "Model configuration ID", "text", true),
    setupField("provider_model", "Provider-native model name", "text", true),
    setupField("display_name", "Display name", "text", false),
    setupField("base_url", "Base URL", "url", true, [], "http://127.0.0.1:PORT/..."),
    setupField("capabilities.context_window", "Context window", "integer", false),
    setupField("capabilities.tool_calling", "Tool calling", "boolean", false),
    setupField("capabilities.structured_output", "Structured output", "boolean", false),
    setupField("capabilities.streaming", "Streaming", "boolean", false),
    setupField("credential_ref.provider", "Secret provider", "text", false, [], undefined, true),
    setupField("credential_ref.secret_id", "Secret ID", "text", false, [], undefined, true),
    setupField("credential_ref.scope", "Secret scope", "text", false, [], "platform", true),
    setupField("credential_ref.version", "Secret version", "text", false, [], undefined, true),
  ];
}

function setupField(
  path: string,
  label: string,
  inputKind: ModelSetupFieldContract["input_kind"],
  required: boolean,
  options: string[] = [],
  placeholder?: string,
  secretReference = false,
): ModelSetupFieldContract {
  return {
    path,
    label,
    input_kind: inputKind,
    required,
    options,
    placeholder: placeholder ?? null,
    secret_reference: secretReference,
  };
}

export function MultiAgentGoalForm({
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
      <div className="form-grid">
        {status.candidate_workspace_ids.length ? <CanonicalSelect label="Workspace" name="workspace_id" values={status.candidate_workspace_ids} /> : null}
        <label>Goal title (optional)<input name="title" placeholder="First multi-agent goal" /></label>
        <label>Goal<input name="objective" required defaultValue="Research two viable approaches, produce a concise result, and review the exact result." /></label>
      </div>
      <div className="context-summary"><span>Built-in team</span><strong>researcher · developer · reviewer</strong></div>
      <p>The platform builds a visible Plan, runs the independent research and approach branches, fans them into execution, and applies canonical Agent Verification to the produced result.</p>
      <button className="primary" disabled={busy}>{busy ? "Running multi-agent goal…" : "Run official multi-agent first run"}</button>
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
      <button className="secondary" disabled={busy}>{busy ? "Creating…" : "Create editable General Assistant"}</button>
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
        <label>Task title (optional)<input name="title" placeholder="General Assistant Task" /></label>
        <label>Objective<input name="objective" required defaultValue="Return one short local response." /></label>
      </div>
      <button className="secondary" disabled={busy}>{busy ? "Running…" : "Run optional single-Agent Task"}</button>
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
