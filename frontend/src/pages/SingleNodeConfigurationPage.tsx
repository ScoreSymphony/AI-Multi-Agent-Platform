import { useCallback, useEffect, useState } from "react";
import type { CanonicalCapability } from "../api/capabilities";
import { ControlPlaneClient } from "../api/client";
import {
  ConfigurationClient,
  type CapabilityAssignmentContent,
  type CapabilityAssignmentRule,
  type CapabilityAssignmentTargetType,
  type CanonicalCapabilityAssignment,
  type CanonicalModelRoutingProfile,
  type ModelRoutingProfilePolicy,
} from "../api/configuration";
import type { CanonicalModel } from "../api/types";
import { AppLink } from "../app/router";
import {
  CheckboxField,
  ConfigurationBar,
  Field,
  MultiResourcePicker,
  NumberField,
  ResourcePicker,
  configurationFingerprint,
  type ResourceOption,
  useUnsavedChanges,
} from "../components/ConfigurationFields";
import { Card, EmptyState, ErrorState, LoadingState, StatusBadge } from "../components/States";

export function RoutingProfileConfigurationPage({
  core,
  configuration,
  profileId,
}: {
  core: ControlPlaneClient;
  configuration: ConfigurationClient;
  profileId?: string;
}) {
  const [loaded, setLoaded] = useState<CanonicalModelRoutingProfile | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [policy, setPolicy] = useState<ModelRoutingProfilePolicy>(() => emptyRoutingPolicy());
  const [models, setModels] = useState<CanonicalModel[]>([]);
  const [projects, setProjects] = useState<ResourceOption[]>([]);
  const [baseline, setBaseline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const snapshot = configurationFingerprint({ name, description, projectId, policy });
  const dirty = baseline !== "" && baseline !== snapshot;
  useUnsavedChanges(dirty);

  const load = useCallback(async () => {
    setError(null);
    const [modelResult, projectResult] = await Promise.allSettled([
      core.listModels({ limit: 100, sort: "display_name", direction: "asc" }),
      core.listProjects({ limit: 100, sort: "name", direction: "asc" }),
    ]);
    setModels(modelResult.status === "fulfilled" ? modelResult.value.items : []);
    setProjects(
      projectResult.status === "fulfilled"
        ? projectResult.value.items.map((project) => ({ value: project.id, label: project.name }))
        : [],
    );

    if (!profileId) {
      const empty = emptyRoutingPolicy();
      setLoaded(null);
      setName("");
      setDescription("");
      setProjectId(null);
      setPolicy(empty);
      setBaseline(
        configurationFingerprint({ name: "", description: "", projectId: null, policy: empty }),
      );
      return;
    }

    try {
      const profile = await configuration.getRoutingProfile(profileId);
      setLoaded(profile);
      setName(profile.revision.name);
      setDescription(profile.revision.description);
      setProjectId(profile.project_id);
      setPolicy(profile.revision.policy);
      setBaseline(
        configurationFingerprint({
          name: profile.revision.name,
          description: profile.revision.description,
          projectId: profile.project_id,
          policy: profile.revision.policy,
        }),
      );
    } catch (nextError) {
      setError(nextError);
    }
  }, [configuration, core, profileId]);

  useEffect(() => {
    void load();
  }, [load]);

  const modelOptions = models
    .filter((model) => model.enabled)
    .map((model) => ({
      value: model.id,
      label: model.display_name,
      description: `${model.location} · ${model.effective_health}`,
    }));

  const save = async () => {
    if (!name.trim()) {
      setError(new Error("Routing profile name is required."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const input = {
        name: name.trim(),
        description: description.trim(),
        policy,
        project_id: projectId,
      };
      const saved = loaded
        ? await configuration.versionRoutingProfile(loaded.id, loaded.current_revision, input)
        : await configuration.createRoutingProfile(input);
      window.location.assign(`/model-routing-profiles/${encodeURIComponent(saved.id)}`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (profileId && !loaded && !error) return <LoadingState label="Loading routing profile…" />;
  if (error && profileId && !loaded) return <ErrorState error={error} onRetry={() => void load()} />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Single-node model configuration</p>
          <h1>{loaded ? name : "Create Model Routing Profile"}</h1>
          <p>
            Configure provider-neutral model requirements and preferences. Provider-private
            settings remain outside canonical routing intent.
          </p>
        </div>
        <StatusBadge value={loaded ? (loaded.enabled ? "enabled" : "disabled") : "new"} />
      </header>

      {error ? <ErrorState error={error} onRetry={() => setError(null)} /> : null}

      <Card title="Profile identity">
        <div className="configuration-grid">
          <Field label="Name">
            <input value={name} onChange={(event) => setName(event.currentTarget.value)} />
          </Field>
          <Field label="Description">
            <textarea
              value={description}
              onChange={(event) => setDescription(event.currentTarget.value)}
            />
          </Field>
          <ResourcePicker
            label="Project scope"
            value={projectId}
            options={projects}
            onChange={setProjectId}
            disabled={Boolean(loaded)}
            hint={loaded ? "Project scope is immutable after creation." : undefined}
          />
        </div>
      </Card>

      <Card title="Routing requirements">
        <div className="configuration-grid">
          <ResourcePicker
            label="Explicit model"
            value={policy.requirements.explicit_model_id}
            options={modelOptions}
            onChange={(explicit_model_id) =>
              setPolicy({
                ...policy,
                requirements: { ...policy.requirements, explicit_model_id },
              })
            }
          />
          <NumberField
            label="Minimum context window"
            value={policy.requirements.min_context_window}
            onChange={(min_context_window) =>
              setPolicy({
                ...policy,
                requirements: { ...policy.requirements, min_context_window },
              })
            }
          />
          <CheckboxField
            label="Local only"
            checked={policy.requirements.local_only}
            onChange={(local_only) =>
              setPolicy({ ...policy, requirements: { ...policy.requirements, local_only } })
            }
          />
          <CheckboxField
            label="Self-hosted only"
            checked={policy.requirements.self_hosted_only}
            onChange={(self_hosted_only) =>
              setPolicy({ ...policy, requirements: { ...policy.requirements, self_hosted_only } })
            }
          />
          <CheckboxField
            label="Tool calling"
            checked={policy.requirements.tool_calling}
            onChange={(tool_calling) =>
              setPolicy({ ...policy, requirements: { ...policy.requirements, tool_calling } })
            }
          />
          <CheckboxField
            label="Structured output"
            checked={policy.requirements.structured_output}
            onChange={(structured_output) =>
              setPolicy({ ...policy, requirements: { ...policy.requirements, structured_output } })
            }
          />
          <CheckboxField
            label="Streaming"
            checked={policy.requirements.streaming}
            onChange={(streaming) =>
              setPolicy({ ...policy, requirements: { ...policy.requirements, streaming } })
            }
          />
          <Field label="Fallback">
            <select
              value={policy.fallback}
              onChange={(event) =>
                setPolicy({ ...policy, fallback: event.currentTarget.value as "route" | "fail" })
              }
            >
              <option value="route">Route to another compatible model</option>
              <option value="fail">Fail when requirements cannot be met</option>
            </select>
          </Field>
        </div>
      </Card>

      <MultiResourcePicker
        label="Preferred models"
        values={policy.preferred_model_ids}
        options={modelOptions}
        onChange={(preferred_model_ids) => setPolicy({ ...policy, preferred_model_ids })}
        hint="Selection is canonical intent; final routing remains server-authoritative."
      />

      {loaded ? (
        <Card title="Lifecycle">
          <div className="actions">
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError(null);
                try {
                  const updated = await configuration.setRoutingProfileEnabled(
                    loaded.id,
                    !loaded.enabled,
                  );
                  setLoaded(updated);
                } catch (nextError) {
                  setError(nextError);
                } finally {
                  setBusy(false);
                }
              }}
            >
              {loaded.enabled ? "Disable profile" : "Enable profile"}
            </button>
            <span>
              Current exact reference: <code>{loaded.exact_ref}</code>
            </span>
          </div>
        </Card>
      ) : null}

      <ConfigurationBar
        dirty={dirty}
        busy={busy}
        onDiscard={() => void load()}
        onSave={() => void save()}
        saveLabel={loaded ? "Save new revision" : "Create routing profile"}
      />
      <div className="actions">
        <AppLink href="/models">Back to Models</AppLink>
      </div>
    </div>
  );
}

export function CapabilityAssignmentConfigurationPage({
  core,
  configuration,
  assignmentId,
}: {
  core: ControlPlaneClient;
  configuration: ConfigurationClient;
  assignmentId?: string;
}) {
  const [loaded, setLoaded] = useState<CanonicalCapabilityAssignment | null>(null);
  const [content, setContent] = useState<CapabilityAssignmentContent>(() => emptyAssignmentContent());
  const [capabilities, setCapabilities] = useState<CanonicalCapability[]>([]);
  const [targetOptions, setTargetOptions] = useState<
    Record<CapabilityAssignmentTargetType, ResourceOption[]>
  >({ agent: [], agent_team: [], project: [] });
  const [baseline, setBaseline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const snapshot = configurationFingerprint(content);
  const dirty = baseline !== "" && baseline !== snapshot;
  useUnsavedChanges(dirty);

  const load = useCallback(async () => {
    setError(null);
    const [capabilityResult, agentResult, teamResult, projectResult] = await Promise.allSettled([
      core.listCapabilities({ limit: 100, sort: "id", direction: "asc" }),
      core.listAgents({ limit: 100, sort: "id", direction: "asc" }),
      core.listAgentTeams({ limit: 100, sort: "id", direction: "asc" }),
      core.listProjects({ limit: 100, sort: "name", direction: "asc" }),
    ]);
    setCapabilities(
      capabilityResult.status === "fulfilled" ? capabilityResult.value.items : [],
    );
    setTargetOptions({
      agent:
        agentResult.status === "fulfilled"
          ? agentResult.value.items.map((agent) => ({
              value: agent.id,
              label: agent.revision.profile.name,
              description: `revision ${agent.current_revision}`,
            }))
          : [],
      agent_team:
        teamResult.status === "fulfilled"
          ? teamResult.value.items.map((team) => ({
              value: team.id,
              label: team.revision.profile.name,
              description: `revision ${team.current_revision}`,
            }))
          : [],
      project:
        projectResult.status === "fulfilled"
          ? projectResult.value.items.map((project) => ({ value: project.id, label: project.name }))
          : [],
    });

    if (!assignmentId) {
      const empty = emptyAssignmentContent();
      setLoaded(null);
      setContent(empty);
      setBaseline(configurationFingerprint(empty));
      return;
    }

    try {
      const assignment = await configuration.getCapabilityAssignment(assignmentId);
      setLoaded(assignment);
      setContent(assignment.revision.content);
      setBaseline(configurationFingerprint(assignment.revision.content));
    } catch (nextError) {
      setError(nextError);
    }
  }, [assignmentId, configuration, core]);

  useEffect(() => {
    void load();
  }, [load]);

  const capabilityOptions = capabilities.map((capability) => ({
    value: capability.id,
    label: capability.name,
    description: capability.available ? "available" : "unavailable",
  }));
  const requiredIds = content.required.map((rule) => rule.capability_id);
  const allowedIds = content.allowed.map((rule) => rule.capability_id);
  const deniedIds = content.denied.map((rule) => rule.capability_id);

  const setBucket = (bucket: "required" | "allowed" | "denied", ids: string[]) => {
    setContent((current) => {
      const existingRules = [...current.required, ...current.allowed, ...current.denied];
      const rules = ids.map(
        (id) => existingRules.find((rule) => rule.capability_id === id) ?? emptyRule(id),
      );
      const required = bucket === "required"
        ? rules
        : current.required.filter((rule) => !ids.includes(rule.capability_id));
      const allowed = bucket === "allowed"
        ? rules
        : current.allowed.filter((rule) => !ids.includes(rule.capability_id));
      const denied = bucket === "denied"
        ? rules
        : current.denied.filter((rule) => !ids.includes(rule.capability_id));
      return { ...current, required, allowed, denied };
    });
  };

  const save = async () => {
    if (!content.target.subject_id) {
      setError(new Error("Select a canonical target before saving."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = loaded
        ? await configuration.reviseCapabilityAssignment(
            loaded.id,
            loaded.current_revision,
            content,
          )
        : await configuration.createCapabilityAssignment({ content });
      window.location.assign(`/capability-assignments/${encodeURIComponent(saved.id)}`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (assignmentId && !loaded && !error) {
    return <LoadingState label="Loading capability assignment…" />;
  }
  if (error && assignmentId && !loaded) {
    return <ErrorState error={error} onRetry={() => void load()} />;
  }

  const securityRelevant = [...content.required, ...content.allowed, ...content.denied].filter(
    (rule) => rule.privileged || rule.approval_required,
  );

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Single-node capability policy</p>
          <h1>{loaded ? loaded.id : "Create Capability Assignment"}</h1>
          <p>
            Configure canonical required, allowed and denied capabilities without exposing
            provider-private invocation paths.
          </p>
        </div>
        <StatusBadge value={loaded ? `revision-${loaded.current_revision}` : "new"} />
      </header>

      {error ? <ErrorState error={error} onRetry={() => setError(null)} /> : null}

      <Card title="Target">
        <div className="configuration-grid">
          <Field label="Target type">
            <select
              disabled={Boolean(loaded)}
              value={content.target.subject_type}
              onChange={(event) =>
                setContent({
                  ...content,
                  target: {
                    subject_type: event.currentTarget.value as CapabilityAssignmentTargetType,
                    subject_id: "",
                  },
                })
              }
            >
              <option value="agent">Agent</option>
              <option value="agent_team">Agent Team</option>
              <option value="project">Project</option>
            </select>
          </Field>
          <ResourcePicker
            label="Target resource"
            value={content.target.subject_id || null}
            options={targetOptions[content.target.subject_type]}
            onChange={(subject_id) =>
              setContent({ ...content, target: { ...content.target, subject_id: subject_id ?? "" } })
            }
            disabled={Boolean(loaded)}
            emptyLabel="Select a target…"
            hint={loaded ? "Assignment target is immutable across revisions." : undefined}
          />
        </div>
      </Card>

      <div className="grid-two">
        <MultiResourcePicker
          label="Required capabilities"
          values={requiredIds}
          options={capabilityOptions}
          onChange={(ids) => setBucket("required", ids)}
        />
        <MultiResourcePicker
          label="Allowed capabilities"
          values={allowedIds}
          options={capabilityOptions}
          onChange={(ids) => setBucket("allowed", ids)}
        />
      </div>
      <MultiResourcePicker
        label="Denied capabilities"
        values={deniedIds}
        options={capabilityOptions}
        onChange={(ids) => setBucket("denied", ids)}
      />

      <Card title="Security implications">
        <p>
          Capability safety, privilege and approval policy remain server-authoritative. Existing
          privilege/approval flags are preserved when a capability moves between buckets.
        </p>
        {securityRelevant.length ? (
          <ul className="plain-list">
            {securityRelevant.map((rule) => (
              <li key={rule.capability_id}>
                <code>{rule.capability_id}</code> — {rule.privileged ? "privileged" : "standard"}
                {rule.approval_required ? "; approval required" : ""}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No stored privileged/approval flags in this revision" />
        )}
      </Card>

      <ConfigurationBar
        dirty={dirty}
        busy={busy}
        onDiscard={() => void load()}
        onSave={() => void save()}
        saveLabel={loaded ? "Save new revision" : "Create assignment"}
      />
      <div className="actions">
        <AppLink href="/tools">Back to Tools</AppLink>
      </div>
    </div>
  );
}

export function emptyRoutingPolicy(): ModelRoutingProfilePolicy {
  return {
    requirements: {
      explicit_model_id: null,
      min_context_window: null,
      tool_calling: false,
      structured_output: false,
      streaming: false,
      modalities: [],
      reasoning: [],
      local_only: false,
      self_hosted_only: false,
    },
    preferred_model_ids: [],
    fallback: "route",
  };
}

export function emptyAssignmentContent(): CapabilityAssignmentContent {
  return {
    target: { subject_type: "agent", subject_id: "" },
    required: [],
    allowed: [],
    denied: [],
  };
}

function emptyRule(capabilityId: string): CapabilityAssignmentRule {
  return {
    capability_id: capabilityId,
    exact_version: null,
    compatibility: null,
    privileged: false,
    approval_required: false,
  };
}
