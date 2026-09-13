import { useCallback, useEffect, useState } from "react";
import type { AgentProfile, CanonicalAgent } from "../../api/agents";
import type { CanonicalCapability } from "../../api/capabilities";
import { ControlPlaneClient } from "../../api/client";
import { ControlPlaneCollectionClient } from "../../api/collections";
import {
  ConfigurationClient,
  type CanonicalModelRoutingProfile,
} from "../../api/configuration";
import type {
  CanonicalModel,
  CanonicalProject,
  CanonicalWorkspaceIdentity,
} from "../../api/types";
import { AppLink } from "../../app/router";
import {
  CheckboxField,
  ConfigurationBar,
  Field,
  MultiResourcePicker,
  NumberField,
  ResourcePicker,
  StringListField,
  configurationFingerprint,
  type ResourceOption,
  useUnsavedChanges,
} from "../../components/ConfigurationFields";
import { Card, ErrorState, LoadingState, StatusBadge } from "../../components/States";
import {
  emptyAgentProfile,
  normalizeAgentProfile,
  updateCapabilityBucket,
  validateAgent,
  withInstructionChanges,
  type CapabilityBucket,
} from "./agentConfigurationState";

const MEMORY_SCOPES = [
  "short_term",
  "task",
  "agent",
  "workspace",
  "user",
  "historical",
  "organization",
] as const;

type AgentEditorMode = "create" | "edit" | "clone";
type NamedResource = { id?: unknown; name?: unknown; title?: unknown; status?: unknown };

interface AgentInventories {
  models: CanonicalModel[];
  routingProfiles: CanonicalModelRoutingProfile[];
  capabilities: CanonicalCapability[];
  projects: CanonicalProject[];
  workspaces: CanonicalWorkspaceIdentity[];
  knowledge: NamedResource[];
}

const EMPTY_INVENTORIES: AgentInventories = {
  models: [],
  routingProfiles: [],
  capabilities: [],
  projects: [],
  workspaces: [],
  knowledge: [],
};

export function AgentConfigurationPage({
  core,
  configuration,
  collections,
  mode,
  agentId,
}: {
  core: ControlPlaneClient;
  configuration: ConfigurationClient;
  collections: ControlPlaneCollectionClient;
  mode: AgentEditorMode;
  agentId?: string;
}) {
  const [loaded, setLoaded] = useState<CanonicalAgent | null>(null);
  const [profile, setProfile] = useState<AgentProfile>(() => emptyAgentProfile());
  const [projectId, setProjectId] = useState<string | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [inventories, setInventories] = useState<AgentInventories>(EMPTY_INVENTORIES);
  const [baseline, setBaseline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [inventoryWarnings, setInventoryWarnings] = useState<string[]>([]);

  const snapshot = configurationFingerprint({ profile, projectId, workspaceId });
  const dirty = baseline !== "" && baseline !== snapshot;
  useUnsavedChanges(dirty);

  const load = useCallback(async () => {
    setError(null);
    const warnings: string[] = [];
    const inventoryResults = await Promise.allSettled([
      core.listModels({ limit: 100, sort: "display_name", direction: "asc" }),
      configuration.listRoutingProfiles({ limit: 100 }),
      core.listCapabilities({ limit: 100, sort: "id", direction: "asc" }),
      core.listProjects({ limit: 100, sort: "name", direction: "asc" }),
      core.listWorkspaces({ limit: 100, sort: "id", direction: "asc" }),
      collections.list<NamedResource>("knowledge", { limit: 100 }),
    ]);
    const next: AgentInventories = { ...EMPTY_INVENTORIES };
    const [models, routing, capabilities, projects, workspaces, knowledge] = inventoryResults;
    if (models.status === "fulfilled") next.models = models.value.items;
    else warnings.push("models");
    if (routing.status === "fulfilled") next.routingProfiles = routing.value.items;
    else warnings.push("model routing profiles");
    if (capabilities.status === "fulfilled") next.capabilities = capabilities.value.items;
    else warnings.push("capabilities");
    if (projects.status === "fulfilled") next.projects = projects.value.items;
    else warnings.push("projects");
    if (workspaces.status === "fulfilled") next.workspaces = workspaces.value.items;
    else warnings.push("workspaces");
    if (knowledge.status === "fulfilled") next.knowledge = knowledge.value.items;
    else warnings.push("knowledge");
    setInventories(next);
    setInventoryWarnings(warnings);

    if (mode === "create") {
      const initial = emptyAgentProfile();
      setLoaded(null);
      setProfile(initial);
      setProjectId(null);
      setWorkspaceId(null);
      setBaseline(configurationFingerprint({ profile: initial, projectId: null, workspaceId: null }));
      return;
    }
    if (!agentId) {
      setError(new Error("Agent ID is required for this editor mode."));
      return;
    }
    try {
      const agent = await core.getAgent(agentId);
      setLoaded(agent);
      setProfile(agent.revision.profile);
      setProjectId(agent.project_id);
      setWorkspaceId(agent.workspace_id);
      setBaseline(configurationFingerprint({
        profile: agent.revision.profile,
        projectId: agent.project_id,
        workspaceId: agent.workspace_id,
      }));
    } catch (nextError) {
      setError(nextError);
    }
  }, [agentId, collections, configuration, core, mode]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    const validation = validateAgent(profile);
    if (validation) {
      setError(new Error(validation));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      let saved: CanonicalAgent;
      if (mode === "create") {
        saved = await configuration.createAgent(normalizeAgentProfile(profile, projectId, workspaceId), {
          project_id: projectId,
          workspace_id: workspaceId,
        });
      } else if (mode === "clone") {
        if (!loaded) throw new Error("Source Agent is not loaded.");
        saved = await configuration.cloneAgent(loaded.id, {
          revision: loaded.current_revision,
          name: profile.name,
          project_id: projectId,
          workspace_id: workspaceId,
        });
      } else {
        if (!loaded) throw new Error("Agent is not loaded.");
        saved = await configuration.updateAgent(
          loaded.id,
          normalizeAgentProfile(profile, projectId, workspaceId),
          loaded.current_revision,
          { project_id: projectId, workspace_id: workspaceId },
        );
      }
      window.location.assign(`/agents/${encodeURIComponent(saved.id)}`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (mode !== "create" && !loaded && !error) return <LoadingState label="Loading Agent configuration…" />;
  if (error && mode !== "create" && !loaded) return <ErrorState error={error} onRetry={() => void load()} />;

  const modelOptions = inventories.models
    .filter((model) => model.enabled)
    .map((model) => ({ value: model.id, label: model.display_name, description: `${model.location} · ${model.effective_health}` }));
  const routingOptions = inventories.routingProfiles
    .filter((routing) => routing.enabled)
    .map((routing) => ({ value: routing.exact_ref, label: routing.revision.name, description: `revision ${routing.current_revision}` }));
  const capabilityOptions = inventories.capabilities.map((capability) => ({ value: capability.id, label: capability.name, description: capability.available ? "available" : "unavailable" }));
  const projectOptions = inventories.projects.map((project) => ({ value: project.id, label: project.name }));
  const workspaceOptions = inventories.workspaces
    .filter((workspace) => projectId === null || workspace.project_id === projectId)
    .map((workspace) => ({ value: workspace.id, label: workspace.id, description: workspace.lifecycle === "canonical" ? workspace.status : "identity only" }));
  const knowledgeOptions = namedOptions(inventories.knowledge);

  const setCapabilityBucket = (bucket: CapabilityBucket, values: string[]) => {
    setProfile((current) => updateCapabilityBucket(current, bucket, values));
  };

  const requiredCapabilities = profile.capabilities.constraints
    .filter((constraint) => constraint.required)
    .map((constraint) => constraint.capability_id);

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Single-node configuration</p>
          <h1>{mode === "create" ? "Create Agent" : mode === "clone" ? "Clone Agent" : `Configure ${profile.name}`}</h1>
          <p>Configure canonical Agent intent. Models, tools, data access and policies remain owned by their platform domains and are resolved by the Control Plane.</p>
        </div>
        <StatusBadge value={mode} />
      </header>

      {inventoryWarnings.length ? (
        <div className="state state-warning">
          <strong>Some selectors are degraded.</strong>
          <p>Unavailable inventories: {inventoryWarnings.join(", ")}. Existing references are preserved.</p>
        </div>
      ) : null}
      {error ? <ErrorState error={error} onRetry={() => setError(null)} /> : null}
      {mode === "clone" ? (
        <div className="state state-warning">
          <strong>Clone preserves the source revision.</strong>
          <p>Name, Project and Workspace can be changed during the canonical clone operation. Edit the new Agent afterwards for further changes.</p>
        </div>
      ) : null}

      <Card title="Identity & scope">
        <div className="configuration-grid">
          <Field label="Name"><input value={profile.name} onChange={(event) => setProfile({ ...profile, name: event.currentTarget.value })} /></Field>
          <Field label="Role"><input disabled={mode === "clone"} value={profile.role} onChange={(event) => setProfile({ ...profile, role: event.currentTarget.value })} /></Field>
          <Field label="Description"><textarea disabled={mode === "clone"} value={profile.description} onChange={(event) => setProfile({ ...profile, description: event.currentTarget.value })} /></Field>
          <CheckboxField label="Enabled" disabled={mode === "clone"} checked={profile.enabled} onChange={(enabled) => setProfile({ ...profile, enabled })} />
          <ResourcePicker label="Project" value={projectId} options={projectOptions} onChange={(value) => {
            setProjectId(value);
            if (workspaceId && !inventories.workspaces.some((workspace) => workspace.id === workspaceId && (value === null || workspace.project_id === value))) setWorkspaceId(null);
          }} />
          <ResourcePicker label="Workspace" value={workspaceId} options={workspaceOptions} onChange={setWorkspaceId} />
        </div>
      </Card>

      {mode === "clone" ? null : (
        <>
          <Card title="Instructions">
            <div className="configuration-grid">
              <Field label="Instruction source">
                <select
                  value={profile.instructions.role.ref ? "reference" : "inline"}
                  onChange={(event) => setProfile({
                    ...profile,
                    instructions: {
                      ...profile.instructions,
                      role: event.currentTarget.value === "reference"
                        ? { content: null, ref: "instruction_ref", version: null }
                        : { content: "Describe this Agent's responsibilities.", ref: null, version: null },
                    },
                  })}
                >
                  <option value="inline">Inline versioned content</option>
                  <option value="reference">Canonical/content reference</option>
                </select>
              </Field>
              {profile.instructions.role.ref ? (
                <>
                  <Field label="Instruction reference"><input value={profile.instructions.role.ref} onChange={(event) => setProfile(withInstructionChanges(profile, { ref: event.currentTarget.value }))} /></Field>
                  <Field label="Instruction version"><input value={profile.instructions.role.version ?? ""} onChange={(event) => setProfile(withInstructionChanges(profile, { version: event.currentTarget.value || null }))} /></Field>
                </>
              ) : (
                <Field label="Instruction content"><textarea rows={6} value={profile.instructions.role.content ?? ""} onChange={(event) => setProfile(withInstructionChanges(profile, { content: event.currentTarget.value }))} /></Field>
              )}
              <StringListField label="Platform constraint refs" values={profile.instructions.platform_constraint_refs} onChange={(platform_constraint_refs) => setProfile({ ...profile, instructions: { ...profile.instructions, platform_constraint_refs } })} />
              <StringListField label="Project instruction refs" values={profile.instructions.project_instruction_refs} onChange={(project_instruction_refs) => setProfile({ ...profile, instructions: { ...profile.instructions, project_instruction_refs } })} />
            </div>
          </Card>

          <Card title="Model policy">
            <div className="configuration-grid">
              <ResourcePicker label="Routing profile" value={profile.model.routing_profile_ref} options={routingOptions} onChange={(routing_profile_ref) => setProfile({ ...profile, model: { ...profile.model, routing_profile_ref, requirements: routing_profile_ref ? { ...profile.model.requirements, explicit_model_id: null } : profile.model.requirements } })} hint="Profiles are pinned to the exact immutable revision shown here." />
              <ResourcePicker label="Explicit model" value={profile.model.requirements.explicit_model_id} options={modelOptions} onChange={(explicit_model_id) => setProfile({ ...profile, model: { ...profile.model, routing_profile_ref: explicit_model_id ? null : profile.model.routing_profile_ref, requirements: { ...profile.model.requirements, explicit_model_id } } })} />
              <NumberField label="Minimum context window" value={profile.model.requirements.min_context_window} onChange={(min_context_window) => setProfile({ ...profile, model: { ...profile.model, requirements: { ...profile.model.requirements, min_context_window } } })} />
              <Field label="Fallback"><select value={profile.model.fallback} onChange={(event) => setProfile({ ...profile, model: { ...profile.model, fallback: event.currentTarget.value } })}><option value="fail">Fail</option><option value="route">Route</option></select></Field>
              <CheckboxField label="Allow task override" checked={profile.model.allow_task_override} onChange={(allow_task_override) => setProfile({ ...profile, model: { ...profile.model, allow_task_override } })} />
              <CheckboxField label="Local only" checked={profile.model.requirements.local_only} onChange={(local_only) => setProfile({ ...profile, model: { ...profile.model, requirements: { ...profile.model.requirements, local_only } } })} />
              <CheckboxField label="Self-hosted only" checked={profile.model.requirements.self_hosted_only} onChange={(self_hosted_only) => setProfile({ ...profile, model: { ...profile.model, requirements: { ...profile.model.requirements, self_hosted_only } } })} />
              <CheckboxField label="Tool calling required" checked={profile.model.requirements.tool_calling} onChange={(tool_calling) => setProfile({ ...profile, model: { ...profile.model, requirements: { ...profile.model.requirements, tool_calling } } })} />
              <CheckboxField label="Structured output required" checked={profile.model.requirements.structured_output} onChange={(structured_output) => setProfile({ ...profile, model: { ...profile.model, requirements: { ...profile.model.requirements, structured_output } } })} />
              <CheckboxField label="Streaming required" checked={profile.model.requirements.streaming} onChange={(streaming) => setProfile({ ...profile, model: { ...profile.model, requirements: { ...profile.model.requirements, streaming } } })} />
              <StringListField label="Modalities" values={profile.model.requirements.modalities} onChange={(modalities) => setProfile({ ...profile, model: { ...profile.model, requirements: { ...profile.model.requirements, modalities } } })} />
              <StringListField label="Reasoning capabilities" values={profile.model.requirements.reasoning} onChange={(reasoning) => setProfile({ ...profile, model: { ...profile.model, requirements: { ...profile.model.requirements, reasoning } } })} />
            </div>
          </Card>

          <div className="grid-two">
            <MultiResourcePicker label="Allowed capabilities" values={profile.capabilities.allowed} options={capabilityOptions} onChange={(values) => setCapabilityBucket("allowed", values)} />
            <MultiResourcePicker label="Required capabilities" values={requiredCapabilities} options={capabilityOptions} onChange={(values) => setCapabilityBucket("required", values)} hint="Required capabilities are also kept in the Agent allowlist." />
          </div>
          <MultiResourcePicker label="Denied capabilities" values={profile.capabilities.denied} options={capabilityOptions} onChange={(values) => setCapabilityBucket("denied", values)} />

          <Card title="Memory & knowledge">
            <div className="configuration-grid">
              <fieldset className="configuration-multi-picker">
                <legend>Memory scopes</legend>
                <div className="configuration-option-list">
                  {MEMORY_SCOPES.map((scope) => (
                    <CheckboxField
                      key={scope}
                      label={scope.replaceAll("_", " ")}
                      checked={profile.data_access.memory_scopes.includes(scope)}
                      onChange={(checked) => setProfile((current) => {
                        const memory_scopes = checked
                          ? [...new Set([...current.data_access.memory_scopes, scope])]
                          : current.data_access.memory_scopes.filter((item) => item !== scope);
                        return {
                          ...current,
                          data_access: {
                            ...current.data_access,
                            memory_scopes,
                            allow_user_memory: scope === "user" ? checked : current.data_access.allow_user_memory,
                          },
                        };
                      })}
                    />
                  ))}
                </div>
              </fieldset>
              <MultiResourcePicker label="Knowledge sources" values={profile.data_access.knowledge_source_ids} options={knowledgeOptions} onChange={(knowledge_source_ids) => setProfile({ ...profile, data_access: { ...profile.data_access, knowledge_source_ids } })} />
              <StringListField label="Memory configuration refs" values={profile.data_access.memory_config_refs} onChange={(memory_config_refs) => setProfile({ ...profile, data_access: { ...profile.data_access, memory_config_refs } })} />
              <CheckboxField label="Allow user memory" checked={profile.data_access.allow_user_memory} onChange={(allow_user_memory) => setProfile((current) => ({ ...current, data_access: { ...current.data_access, allow_user_memory, memory_scopes: allow_user_memory ? current.data_access.memory_scopes : current.data_access.memory_scopes.filter((scope) => scope !== "user") } }))} />
            </div>
          </Card>

          <Card title="Policy & advanced constraints">
            <div className="configuration-grid">
              <Field label="Authorization profile ref"><input value={profile.policy_hooks.authorization_profile_ref ?? ""} onChange={(event) => setProfile({ ...profile, policy_hooks: { ...profile.policy_hooks, authorization_profile_ref: event.currentTarget.value || null } })} /></Field>
              <StringListField label="Verification policy refs" values={profile.policy_hooks.verification_policy_refs} onChange={(verification_policy_refs) => setProfile({ ...profile, policy_hooks: { ...profile.policy_hooks, verification_policy_refs } })} />
            </div>
            {profile.capabilities.constraints.some((item) => item.exact_version || item.minimum_version || item.maximum_version || item.required_features.length || item.approval_ref) ? (
              <div className="state state-warning">
                <strong>Advanced capability constraints preserved.</strong>
                <p>Existing version/features/approval constraints remain attached to selected capabilities even though this focused editor does not rewrite those advanced fields.</p>
              </div>
            ) : null}
          </Card>
        </>
      )}

      <ConfigurationBar dirty={dirty} busy={busy} onDiscard={() => void load()} onSave={() => void save()} saveLabel={mode === "clone" ? "Clone Agent" : mode === "create" ? "Create Agent" : "Save new revision"} />
      <div className="actions"><AppLink href={loaded ? `/agents/${encodeURIComponent(loaded.id)}` : "/agents"}>Cancel</AppLink></div>
    </div>
  );
}

function namedOptions(resources: NamedResource[]): ResourceOption[] {
  return resources.flatMap((resource) => {
    if (typeof resource.id !== "string") return [];
    const name = typeof resource.name === "string" ? resource.name : typeof resource.title === "string" ? resource.title : resource.id;
    const status = typeof resource.status === "string" ? resource.status : undefined;
    return [{ value: resource.id, label: name, description: status }];
  });
}
