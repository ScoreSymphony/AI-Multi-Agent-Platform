import { useCallback, useEffect, useState } from "react";
import type { AgentTeamMember, AgentTeamProfile, CanonicalAgent, CanonicalAgentTeam } from "../../api/agents";
import type { CanonicalCapability } from "../../api/capabilities";
import { ControlPlaneClient } from "../../api/client";
import { ConfigurationClient } from "../../api/configuration";
import type { CanonicalProject, CanonicalWorkspaceIdentity } from "../../api/types";
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
  useUnsavedChanges,
} from "../../components/ConfigurationFields";
import { Card, ErrorState, LoadingState, StatusBadge } from "../../components/States";
import {
  emptyTeamProfile,
  validateTeam,
  withMemberChanges,
} from "./agentConfigurationState";

type TeamEditorMode = "create" | "edit";

export function AgentTeamConfigurationPage({
  core,
  configuration,
  mode,
  teamId,
}: {
  core: ControlPlaneClient;
  configuration: ConfigurationClient;
  mode: TeamEditorMode;
  teamId?: string;
}) {
  const [loaded, setLoaded] = useState<CanonicalAgentTeam | null>(null);
  const [profile, setProfile] = useState<AgentTeamProfile>(() => emptyTeamProfile());
  const [agents, setAgents] = useState<CanonicalAgent[]>([]);
  const [capabilities, setCapabilities] = useState<CanonicalCapability[]>([]);
  const [projects, setProjects] = useState<CanonicalProject[]>([]);
  const [workspaces, setWorkspaces] = useState<CanonicalWorkspaceIdentity[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [baseline, setBaseline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const snapshot = configurationFingerprint({ profile, projectId, workspaceId });
  const dirty = baseline !== "" && baseline !== snapshot;
  useUnsavedChanges(dirty);

  const load = useCallback(async () => {
    setError(null);
    const [agentResult, capabilityResult, projectResult, workspaceResult] = await Promise.allSettled([
      core.listAgents({ limit: 100, sort: "id", direction: "asc" }),
      core.listCapabilities({ limit: 100, sort: "id", direction: "asc" }),
      core.listProjects({ limit: 100, sort: "name", direction: "asc" }),
      core.listWorkspaces({ limit: 100, sort: "id", direction: "asc" }),
    ]);
    if (agentResult.status === "fulfilled") setAgents(agentResult.value.items);
    if (capabilityResult.status === "fulfilled") setCapabilities(capabilityResult.value.items);
    if (projectResult.status === "fulfilled") setProjects(projectResult.value.items);
    if (workspaceResult.status === "fulfilled") setWorkspaces(workspaceResult.value.items);

    if (mode === "create") {
      const initial = emptyTeamProfile();
      setLoaded(null);
      setProfile(initial);
      setProjectId(null);
      setWorkspaceId(null);
      setBaseline(configurationFingerprint({ profile: initial, projectId: null, workspaceId: null }));
      return;
    }
    if (!teamId) {
      setError(new Error("Team ID is required for edit mode."));
      return;
    }
    try {
      const team = await core.getAgentTeam(teamId);
      setLoaded(team);
      setProfile(team.revision.profile);
      setProjectId(team.project_id);
      setWorkspaceId(team.workspace_id);
      setBaseline(configurationFingerprint({ profile: team.revision.profile, projectId: team.project_id, workspaceId: team.workspace_id }));
    } catch (nextError) {
      setError(nextError);
    }
  }, [configuration, core, mode, teamId]);

  useEffect(() => void load(), [load]);

  const selectedIds = new Set(profile.members.map((member) => member.agent.agent_id));
  const capabilityOptions = capabilities.map((capability) => ({ value: capability.id, label: capability.name, description: capability.available ? "available" : "unavailable" }));
  const projectOptions = projects.map((project) => ({ value: project.id, label: project.name }));
  const workspaceOptions = workspaces.filter((workspace) => projectId === null || workspace.project_id === projectId).map((workspace) => ({ value: workspace.id, label: workspace.id }));
  const agentOptions = agents.map((agent) => ({ value: agent.id, label: agent.revision.profile.name, description: `revision ${agent.current_revision} · ${agent.revision.profile.role}` }));

  const removeMember = (agentId: string) => {
    setProfile((current) => ({
      ...current,
      members: current.members
        .filter((member) => member.agent.agent_id !== agentId)
        .map((member) => ({ ...member, can_delegate_to: member.can_delegate_to.filter((targetId) => targetId !== agentId) })),
      leader_agent_id: current.leader_agent_id === agentId ? null : current.leader_agent_id,
    }));
  };

  const toggleMember = (agent: CanonicalAgent, checked: boolean) => {
    if (!checked) {
      removeMember(agent.id);
      return;
    }
    setProfile((current) => {
      const member: AgentTeamMember = {
        agent: { agent_id: agent.id, revision: agent.current_revision },
        role: agent.revision.profile.role || "member",
        required: true,
        can_delegate_to: [],
      };
      return { ...current, members: [...current.members, member] };
    });
  };

  const save = async () => {
    const validation = validateTeam(profile);
    if (validation) {
      setError(new Error(validation));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = mode === "create"
        ? await configuration.createAgentTeam(profile, { project_id: projectId, workspace_id: workspaceId })
        : await configuration.updateAgentTeam(loaded!.id, profile, loaded!.current_revision, { project_id: projectId, workspace_id: workspaceId });
      window.location.assign(`/agent-teams/${encodeURIComponent(saved.id)}`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (mode === "edit" && !loaded && !error) return <LoadingState label="Loading Team configuration…" />;
  if (error && mode === "edit" && !loaded) return <ErrorState error={error} onRetry={() => void load()} />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Single-node configuration</p>
          <h1>{mode === "create" ? "Create Agent Team" : `Configure ${profile.name}`}</h1>
          <p>Pin exact Agent revisions, delegation relationships, shared capabilities and Team runtime limits.</p>
        </div>
        <StatusBadge value={mode} />
      </header>
      {error ? <ErrorState error={error} onRetry={() => setError(null)} /> : null}

      <Card title="Team identity & scope">
        <div className="configuration-grid">
          <Field label="Name"><input value={profile.name} onChange={(event) => setProfile({ ...profile, name: event.currentTarget.value })} /></Field>
          <Field label="Description"><textarea value={profile.description} onChange={(event) => setProfile({ ...profile, description: event.currentTarget.value })} /></Field>
          <CheckboxField label="Enabled" checked={profile.enabled} onChange={(enabled) => setProfile({ ...profile, enabled })} />
          <ResourcePicker label="Project" value={projectId} options={projectOptions} onChange={(value) => { setProjectId(value); if (workspaceId && !workspaces.some((workspace) => workspace.id === workspaceId && (value === null || workspace.project_id === value))) setWorkspaceId(null); }} />
          <ResourcePicker label="Workspace" value={workspaceId} options={workspaceOptions} onChange={setWorkspaceId} />
          <Field label="Coordination policy ref"><input value={profile.coordination_policy_ref ?? ""} onChange={(event) => setProfile({ ...profile, coordination_policy_ref: event.currentTarget.value || null })} /></Field>
        </div>
      </Card>

      <Card title="Pinned Agent members">
        <div className="configuration-option-list member-picker">
          {agents.map((agent) => (
            <label key={agent.id} className="configuration-option">
              <input type="checkbox" checked={selectedIds.has(agent.id)} onChange={(event) => toggleMember(agent, event.currentTarget.checked)} />
              <span><strong>{agent.revision.profile.name}</strong><small>{agent.id} · revision {agent.current_revision}</small></span>
            </label>
          ))}
        </div>
        {profile.members.map((member) => {
          const delegationOptions = agentOptions.filter((option) => selectedIds.has(option.value) && option.value !== member.agent.agent_id);
          return (
            <div className="team-member-editor" key={member.agent.agent_id}>
              <div>
                <strong>{agents.find((agent) => agent.id === member.agent.agent_id)?.revision.profile.name ?? member.agent.agent_id}</strong>
                <small> pinned revision {member.agent.revision}</small>
                <button type="button" onClick={() => removeMember(member.agent.agent_id)}>Remove member</button>
              </div>
              <Field label="Team role"><input value={member.role} onChange={(event) => setProfile(withMemberChanges(profile, member.agent.agent_id, { role: event.currentTarget.value }))} /></Field>
              <CheckboxField label="Required member" checked={member.required} onChange={(required) => setProfile(withMemberChanges(profile, member.agent.agent_id, { required }))} />
              <MultiResourcePicker label="Can delegate to" values={member.can_delegate_to} options={delegationOptions} onChange={(can_delegate_to) => setProfile(withMemberChanges(profile, member.agent.agent_id, { can_delegate_to }))} hint="Delegation targets must be other pinned Team members." />
            </div>
          );
        })}
      </Card>

      <Card title="Coordination & runtime limits">
        <div className="configuration-grid">
          <ResourcePicker label="Leader" value={profile.leader_agent_id} options={agentOptions.filter((option) => selectedIds.has(option.value))} onChange={(leader_agent_id) => setProfile({ ...profile, leader_agent_id })} />
          <Field label="Unavailable member policy"><select value={profile.unavailable_member_policy} onChange={(event) => setProfile({ ...profile, unavailable_member_policy: event.currentTarget.value })}><option value="fail">Fail</option><option value="skip_optional">Skip optional members</option></select></Field>
          <NumberField label="Max parallel Agents" value={profile.max_parallel_agents} onChange={(max_parallel_agents) => setProfile({ ...profile, max_parallel_agents })} />
          <NumberField label="Max Steps" value={profile.max_steps} onChange={(max_steps) => setProfile({ ...profile, max_steps })} />
          <MultiResourcePicker label="Shared capabilities" values={profile.shared_capability_ids} options={capabilityOptions} onChange={(shared_capability_ids) => setProfile({ ...profile, shared_capability_ids })} />
          <StringListField label="Shared resource refs" values={profile.shared_resource_refs} onChange={(shared_resource_refs) => setProfile({ ...profile, shared_resource_refs })} />
        </div>
      </Card>

      <ConfigurationBar dirty={dirty} busy={busy} onDiscard={() => void load()} onSave={() => void save()} saveLabel={mode === "create" ? "Create Team" : "Save new revision"} />
      <div className="actions"><AppLink href={loaded ? `/agent-teams/${encodeURIComponent(loaded.id)}` : "/agent-teams"}>Cancel</AppLink></div>
    </div>
  );
}
