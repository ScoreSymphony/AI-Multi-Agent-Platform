import { useCallback, useEffect, useState } from "react";
import type { CanonicalAgent, CanonicalAgentRun, CanonicalAgentTeam } from "../api/agents";
import { ControlPlaneClient } from "../api/client";
import type { Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  Card,
  CanonicalId,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const AGENT_QUERY_KEY = "agents:id:asc";
const AGENT_RUN_QUERY_KEY = "agent-runs:id:desc";
const TEAM_QUERY_KEY = "agent-teams:id:asc";

export function AgentsPage({ client }: { client: ControlPlaneClient }) {
  const [agents, setAgents] = useState<Page<CanonicalAgent> | null>(null);
  const [runs, setRuns] = useState<Page<CanonicalAgentRun> | null>(null);
  const [agentError, setAgentError] = useState<unknown>(null);
  const [runError, setRunError] = useState<unknown>(null);
  const agentPagination = useCursorPagination(AGENT_QUERY_KEY);
  const runPagination = useCursorPagination(AGENT_RUN_QUERY_KEY);

  const loadAgents = useCallback(async () => {
    try {
      setAgents(
        await client.listAgents({
          limit: 100,
          cursor: agentPagination.cursor,
          sort: "id",
          direction: "asc",
        }),
      );
      setAgentError(null);
    } catch (error) {
      setAgentError(error);
    }
  }, [agentPagination.cursor, client]);

  const loadRuns = useCallback(async () => {
    try {
      setRuns(
        await client.listAgentRuns({
          limit: 100,
          cursor: runPagination.cursor,
          sort: "id",
          direction: "desc",
        }),
      );
      setRunError(null);
    } catch (error) {
      setRunError(error);
    }
  }, [client, runPagination.cursor]);

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  if (!agents && !runs && !agentError && !runError) return <LoadingState />;

  const enabledOnPage = agents?.items.filter((agent) => agent.revision.profile.enabled).length ?? "—";
  const runningOnPage = runs?.items.filter((run) => ["starting", "running"].includes(run.status)).length ?? "—";

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical agents</p>
        <h1>Agents</h1>
        <p>
          Durable Agent definitions and exact runtime revision evidence from the platform-owned
          Control Plane. Orchestrator-private session identity is never used for navigation.
        </p>
      </header>

      <div className="metrics">
        <Metric label="Agents" value={agents?.total ?? "—"} />
        <Metric label="Enabled on page" value={enabledOnPage} />
        <Metric label="Agent runs" value={runs?.total ?? "—"} />
        <Metric label="Active on page" value={runningOnPage} />
      </div>

      <Card title="Agent definitions">
        {agentError ? <ErrorState error={agentError} onRetry={() => void loadAgents()} /> : null}
        {agents ? <AgentTable agents={agents.items} /> : agentError ? null : <LoadingState />}
        {agents ? (
          <PaginationControls
            page={agents}
            pageNumber={agentPagination.pageNumber}
            hasPrevious={agentPagination.hasPrevious}
            onPrevious={agentPagination.previous}
            onRefresh={() => void loadAgents()}
            onNext={() => agentPagination.next(agents.next_cursor)}
          />
        ) : null}
      </Card>

      <Card title="Agent runtime evidence">
        {runError ? <ErrorState error={runError} onRetry={() => void loadRuns()} /> : null}
        {runs ? <AgentRunTable runs={runs.items} /> : runError ? null : <LoadingState />}
        {runs ? (
          <PaginationControls
            page={runs}
            pageNumber={runPagination.pageNumber}
            hasPrevious={runPagination.hasPrevious}
            onPrevious={runPagination.previous}
            onRefresh={() => void loadRuns()}
            onNext={() => runPagination.next(runs.next_cursor)}
          />
        ) : null}
      </Card>
    </div>
  );
}

export function AgentDetailPage({
  client,
  agentId,
}: {
  client: ControlPlaneClient;
  agentId: string;
}) {
  const [agent, setAgent] = useState<CanonicalAgent | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setAgent(await client.getAgent(agentId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [agentId, client]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !agent) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!agent) return <LoadingState />;

  const profile = agent.revision.profile;
  const requiredCapabilities = profile.capabilities.constraints
    .filter((constraint) => constraint.required)
    .map((constraint) => constraint.capability_id);
  const modelPolicy = profile.model.requirements.explicit_model_id
    ?? profile.model.routing_profile_ref
    ?? "router policy";

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Agent definition</p>
          <h1>{profile.name}</h1>
          <CanonicalId value={agent.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={profile.enabled ? "enabled" : "disabled"} />
          <span>revision {agent.current_revision}</span>
        </div>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <div className="grid-two">
        <Card title="Definition">
          <DefinitionList
            values={{
              role: profile.role,
              description: profile.description || "—",
              owner: `${agent.owner_ref.type}:${agent.owner_ref.id}`,
              project: agent.project_id ?? "—",
              workspace: agent.workspace_id ?? "—",
              revision: agent.revision.revision,
              updated: formatDate(agent.updated_at),
            }}
          />
        </Card>
        <Card title="Model policy">
          <DefinitionList
            values={{
              assignment: modelPolicy,
              fallback: profile.model.fallback,
              task_override: profile.model.allow_task_override ? "allowed" : "not allowed",
              local_only: profile.model.requirements.local_only ? "yes" : "no",
              self_hosted_only: profile.model.requirements.self_hosted_only ? "yes" : "no",
            }}
          />
        </Card>
      </div>
      <div className="grid-two">
        <Card title="Capabilities">
          <ReferenceValues label="Allowed" values={profile.capabilities.allowed} />
          <ReferenceValues label="Denied" values={profile.capabilities.denied} />
          <ReferenceValues label="Required" values={requiredCapabilities} />
        </Card>
        <Card title="Memory & knowledge">
          <ReferenceValues label="Memory scopes" values={profile.data_access.memory_scopes} />
          <ReferenceValues label="Memory configs" values={profile.data_access.memory_config_refs} />
          <ReferenceValues label="Knowledge sources" values={profile.data_access.knowledge_source_ids} />
        </Card>
      </div>
      <div className="grid-two">
        <Card title="Instruction source">
          <DefinitionList
            values={{
              source: profile.instructions.role.ref ? "reference" : "inline versioned content",
              ref: profile.instructions.role.ref ?? "—",
              version: profile.instructions.role.version ?? "—",
              platform_constraints: profile.instructions.platform_constraint_refs.length,
              project_instructions: profile.instructions.project_instruction_refs.length,
            }}
          />
          <small>Instruction content is not rendered by default on the inventory surface.</small>
        </Card>
        <Card title="Policy hooks">
          <DefinitionList
            values={{
              authorization_profile: profile.policy_hooks.authorization_profile_ref ?? "—",
              verification_policies: profile.policy_hooks.verification_policy_refs.join(", ") || "—",
            }}
          />
        </Card>
      </div>
    </div>
  );
}

export function AgentTeamsPage({ client }: { client: ControlPlaneClient }) {
  const [teams, setTeams] = useState<Page<CanonicalAgentTeam> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const pagination = useCursorPagination(TEAM_QUERY_KEY);

  const load = useCallback(async () => {
    try {
      setTeams(
        await client.listAgentTeams({
          limit: 100,
          cursor: pagination.cursor,
          sort: "id",
          direction: "asc",
        }),
      );
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, pagination.cursor]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical coordination</p>
        <h1>Agent Teams</h1>
        <p>Versioned Team definitions with exact member Agent revisions and delegation policy.</p>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {!teams && !error ? <LoadingState /> : null}
      {teams ? (
        <Card title="Team definitions">
          <TeamTable teams={teams.items} />
          <PaginationControls
            page={teams}
            pageNumber={pagination.pageNumber}
            hasPrevious={pagination.hasPrevious}
            onPrevious={pagination.previous}
            onRefresh={() => void load()}
            onNext={() => pagination.next(teams.next_cursor)}
          />
        </Card>
      ) : null}
    </div>
  );
}

export function AgentTeamDetailPage({
  client,
  teamId,
}: {
  client: ControlPlaneClient;
  teamId: string;
}) {
  const [team, setTeam] = useState<CanonicalAgentTeam | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setTeam(await client.getAgentTeam(teamId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, teamId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !team) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!team) return <LoadingState />;

  const profile = team.revision.profile;
  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Agent Team</p>
          <h1>{profile.name}</h1>
          <CanonicalId value={team.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={profile.enabled ? "enabled" : "disabled"} />
          <span>revision {team.current_revision}</span>
        </div>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <div className="grid-two">
        <Card title="Team policy">
          <DefinitionList
            values={{
              description: profile.description || "—",
              owner: `${team.owner_ref.type}:${team.owner_ref.id}`,
              project: team.project_id ?? "—",
              workspace: team.workspace_id ?? "—",
              coordination: profile.coordination_policy_ref ?? "—",
              unavailable_members: profile.unavailable_member_policy,
            }}
          />
        </Card>
        <Card title="Limits & shared capabilities">
          <DefinitionList
            values={{
              leader: profile.leader_agent_id ?? "—",
              max_parallel_agents: profile.max_parallel_agents ?? "—",
              max_steps: profile.max_steps ?? "—",
              shared_capabilities: profile.shared_capability_ids.join(", ") || "—",
            }}
          />
        </Card>
      </div>
      <Card title="Pinned members">
        {profile.members.length === 0 ? (
          <EmptyState title="No Team members" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Agent</th><th>Revision</th><th>Role</th><th>Required</th><th>Delegation</th></tr>
              </thead>
              <tbody>
                {profile.members.map((member) => (
                  <tr key={member.agent.agent_id}>
                    <td>
                      <AppLink href={`/agents/${encodeURIComponent(member.agent.agent_id)}`}>
                        {member.agent.agent_id}
                      </AppLink>
                    </td>
                    <td>{member.agent.revision}</td>
                    <td>{member.role}</td>
                    <td>{member.required ? "yes" : "no"}</td>
                    <td>{member.can_delegate_to.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function AgentTable({ agents }: { agents: CanonicalAgent[] }) {
  if (agents.length === 0) return <EmptyState title="No Agent definitions" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Agent</th><th>Role</th><th>Revision</th><th>Status</th><th>Project</th></tr></thead>
        <tbody>
          {agents.map((agent) => (
            <tr key={agent.id}>
              <td>
                <AppLink href={`/agents/${encodeURIComponent(agent.id)}`}>
                  {agent.revision.profile.name}
                </AppLink>
                <div><code>{agent.id}</code></div>
              </td>
              <td>{agent.revision.profile.role}</td>
              <td>{agent.current_revision}</td>
              <td><StatusBadge value={agent.revision.profile.enabled ? "enabled" : "disabled"} /></td>
              <td>{agent.project_id ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TeamTable({ teams }: { teams: CanonicalAgentTeam[] }) {
  if (teams.length === 0) return <EmptyState title="No Agent Teams" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Team</th><th>Revision</th><th>Members</th><th>Status</th><th>Leader</th></tr></thead>
        <tbody>
          {teams.map((team) => (
            <tr key={team.id}>
              <td>
                <AppLink href={`/agent-teams/${encodeURIComponent(team.id)}`}>
                  {team.revision.profile.name}
                </AppLink>
                <div><code>{team.id}</code></div>
              </td>
              <td>{team.current_revision}</td>
              <td>{team.revision.profile.members.length}</td>
              <td><StatusBadge value={team.revision.profile.enabled ? "enabled" : "disabled"} /></td>
              <td>{team.revision.profile.leader_agent_id ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AgentRunTable({ runs }: { runs: CanonicalAgentRun[] }) {
  if (runs.length === 0) return <EmptyState title="No Agent runtime records" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Agent revision</th><th>Status</th><th>Task</th><th>Run</th><th>Model</th></tr></thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>
                <AppLink href={`/agents/${encodeURIComponent(run.agent.agent_id)}`}>
                  {run.agent.agent_id}
                </AppLink>
                <div>revision {run.agent.revision}</div>
              </td>
              <td><StatusBadge value={run.status} /></td>
              <td><AppLink href={`/tasks/${encodeURIComponent(run.task_id)}`}>{run.task_id}</AppLink></td>
              <td><AppLink href={`/runs/${encodeURIComponent(run.run_id)}`}>{run.run_id}</AppLink></td>
              <td>{run.selected_model_config_id ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReferenceValues({ label, values }: { label: string; values: string[] }) {
  return (
    <div className="stack compact-stack">
      <strong>{label}</strong>
      {values.length ? <div className="chips">{values.map((value) => <code key={value}>{value}</code>)}</div> : <span>—</span>}
    </div>
  );
}

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return (
    <dl className="definition-list">
      {Object.entries(values).map(([label, value]) => (
        <div key={label}><dt>{label.replaceAll("_", " ")}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
