import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import type {
  CanonicalCollaborationTeam,
  CanonicalInvitation,
  CanonicalMembership,
  CanonicalOrganization,
  CanonicalOrganizationAuditEvent,
  OrganizationActorType,
} from "../api/organizations";
import { OrganizationClient } from "../api/organizations";
import type { Page } from "../api/types";
import {
  CanonicalId,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";
import { OrganizationConfigurationPanel } from "./OrganizationConfigurationPanel";
import { OrganizationResourcesPanel } from "./OrganizationResourcesPanel";

const CONTEXT_KEY = "agent-platform:organization-context";

interface CollaborationContext {
  organizationId: string | null;
  teamId: string | null;
}

interface OrganizationData {
  teams: Page<CanonicalCollaborationTeam>;
  memberships: Page<CanonicalMembership>;
  invitations: Page<CanonicalInvitation>;
  audit: Page<CanonicalOrganizationAuditEvent> | null;
}

export function OrganizationsPage({ client }: { client: OrganizationClient }) {
  const [organizations, setOrganizations] = useState<Page<CanonicalOrganization> | null>(null);
  const [context, setContext] = useState<CollaborationContext>(() => loadContext());
  const [data, setData] = useState<OrganizationData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [mutationError, setMutationError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const loadOrganizations = useCallback(async () => {
    try {
      const page = await client.listOrganizations({
        limit: 250,
        sort: "updated_at",
        direction: "desc",
      });
      setOrganizations(page);
      setError(null);
      setContext((current) => {
        if (current.organizationId && page.items.some((item) => item.id === current.organizationId)) {
          return current;
        }
        const active = page.items.find((item) => item.status === "active") ?? page.items[0];
        return { organizationId: active?.id ?? null, teamId: null };
      });
    } catch (nextError) {
      setError(nextError);
    }
  }, [client]);

  const loadOrganizationData = useCallback(async () => {
    if (!context.organizationId) {
      setData(null);
      return;
    }
    try {
      const [teams, memberships, invitations, audit] = await Promise.all([
        client.listTeams(context.organizationId),
        client.listMemberships(context.organizationId),
        client.listInvitations(context.organizationId),
        client.listAudit(context.organizationId).catch(() => null),
      ]);
      setData({ teams, memberships, invitations, audit });
      setError(null);
      setContext((current) => {
        if (!current.teamId || teams.items.some((team) => team.id === current.teamId)) return current;
        return { ...current, teamId: null };
      });
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, context.organizationId]);

  useEffect(() => {
    void loadOrganizations();
  }, [loadOrganizations]);

  useEffect(() => {
    persistContext(context);
    void loadOrganizationData();
  }, [context, loadOrganizationData]);

  const selectedOrganization = useMemo(
    () => organizations?.items.find((item) => item.id === context.organizationId) ?? null,
    [context.organizationId, organizations],
  );
  const selectedTeam = useMemo(
    () => data?.teams.items.find((item) => item.id === context.teamId) ?? null,
    [context.teamId, data],
  );
  const scopedMemberships = useMemo(() => {
    if (!data) return [];
    if (!context.teamId) return data.memberships.items;
    return data.memberships.items.filter((membership) => membership.team_id === context.teamId);
  }, [context.teamId, data]);

  const refresh = useCallback(async () => {
    await loadOrganizations();
    await loadOrganizationData();
  }, [loadOrganizationData, loadOrganizations]);

  const mutate = useCallback(
    async (key: string, operation: () => Promise<unknown>) => {
      setBusy(key);
      setMutationError(null);
      try {
        await operation();
        await refresh();
      } catch (nextError) {
        setMutationError(nextError);
      } finally {
        setBusy(null);
      }
    },
    [refresh],
  );

  const createOrganization = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await mutate("organization.create", async () => {
      const organization = await client.createOrganization({
        name: required(form, "name"),
        display_name: optional(form, "display_name"),
        administrator_actor_ids: csv(form, "administrator_actor_ids"),
      });
      setContext({ organizationId: organization.id, teamId: null });
      event.currentTarget.reset();
    });
  };

  const createTeam = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!context.organizationId) return;
    const form = new FormData(event.currentTarget);
    await mutate("team.create", async () => {
      const team = await client.createTeam(context.organizationId!, {
        name: required(form, "name"),
        description: optional(form, "description"),
        parent_team_id: optional(form, "parent_team_id"),
      });
      setContext((current) => ({ ...current, teamId: team.id }));
      event.currentTarget.reset();
    });
  };

  const addMember = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!context.organizationId) return;
    const form = new FormData(event.currentTarget);
    await mutate("membership.add", async () => {
      await client.addMembership(context.organizationId!, {
        actor_id: required(form, "actor_id"),
        actor_type: String(form.get("actor_type") ?? "human") as OrganizationActorType,
        team_id: optional(form, "team_id") ?? context.teamId ?? undefined,
        role_refs: csv(form, "role_refs"),
        policy_refs: csv(form, "policy_refs"),
      });
      event.currentTarget.reset();
    });
  };

  const inviteMember = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!context.organizationId) return;
    const form = new FormData(event.currentTarget);
    const identity = optional(form, "intended_identity_ref");
    const email = optional(form, "intended_email_ref");
    if (!identity && !email) {
      setMutationError(new Error("Provide an intended identity or email reference."));
      return;
    }
    await mutate("invitation.create", async () => {
      await client.createInvitation(context.organizationId!, {
        intended_identity_ref: identity,
        intended_email_ref: email,
        team_id: optional(form, "team_id") ?? context.teamId ?? undefined,
        role_refs: csv(form, "role_refs"),
        policy_refs: csv(form, "policy_refs"),
        expires_at: expiresAt(form),
        token_ref: `secret:invitation:${crypto.randomUUID()}`,
      });
      event.currentTarget.reset();
    });
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Collaboration scope</p>
        <h1>Organizations & teams</h1>
        <p>
          Manage collaboration structure, memberships, invitations and policy-backed roles while
          authorization remains owned by the canonical policy engine.
        </p>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void refresh()} /> : null}
      {mutationError ? <ErrorState error={mutationError} /> : null}

      <Card title="Current collaboration context">
        {!organizations ? (
          <LoadingState />
        ) : (
          <div className="form-grid">
            <label>
              Organization
              <select
                aria-label="Current organization"
                value={context.organizationId ?? ""}
                onChange={(event) =>
                  setContext({ organizationId: event.target.value || null, teamId: null })
                }
              >
                <option value="">Personal / no organization</option>
                {organizations.items.map((organization) => (
                  <option key={organization.id} value={organization.id}>
                    {organization.display_name || organization.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Team
              <select
                aria-label="Current team"
                value={context.teamId ?? ""}
                disabled={!context.organizationId || !data}
                onChange={(event) =>
                  setContext((current) => ({ ...current, teamId: event.target.value || null }))
                }
              >
                <option value="">Organization-wide</option>
                {data?.teams.items.map((team) => (
                  <option key={team.id} value={team.id}>
                    {team.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="context-summary">
              <span>Scope</span>
              <strong>
                {selectedOrganization
                  ? `${selectedOrganization.name}${selectedTeam ? ` / ${selectedTeam.name}` : ""}`
                  : "Personal"}
              </strong>
            </div>
          </div>
        )}
      </Card>

      <Card title="Create organization">
        <form className="form-grid" onSubmit={createOrganization}>
          <label>Name<input name="name" required /></label>
          <label>Display name<input name="display_name" /></label>
          <label>
            Administrator actor refs
            <input name="administrator_actor_ids" placeholder="user:alice, service:ops" />
          </label>
          <button className="primary" disabled={busy !== null}>
            {busy === "organization.create" ? "Creating…" : "Create organization"}
          </button>
        </form>
      </Card>

      {!context.organizationId ? (
        <Card title="Personal scope">
          <EmptyState
            title="No organization selected"
            detail="Personal work remains first-class. Select or create an organization only when collaboration is needed."
          />
        </Card>
      ) : !selectedOrganization || !data ? (
        <LoadingState />
      ) : (
        <>
          <OrganizationSummary
            organization={selectedOrganization}
            team={selectedTeam}
            onArchive={() =>
              void mutate("organization.archive", () => client.archiveOrganization(selectedOrganization.id))
            }
            archiving={busy === "organization.archive"}
          />

          <OrganizationConfigurationPanel
            client={client}
            organization={selectedOrganization}
            team={selectedTeam}
            teams={data.teams.items}
            onChanged={refresh}
          />

          <div className="two-column">
            <Card title="Create team">
              <form className="form-grid" onSubmit={createTeam}>
                <label>Name<input name="name" required /></label>
                <label>Description<input name="description" /></label>
                <label>
                  Parent team
                  <select name="parent_team_id" defaultValue="">
                    <option value="">None</option>
                    {data.teams.items.map((team) => (
                      <option key={team.id} value={team.id}>{team.name}</option>
                    ))}
                  </select>
                </label>
                <button className="primary" disabled={busy !== null}>
                  {busy === "team.create" ? "Creating…" : "Create team"}
                </button>
              </form>
            </Card>

            <Card title="Add member">
              <form className="form-grid" onSubmit={addMember}>
                <label>Actor ref<input name="actor_id" placeholder="user:alice" required /></label>
                <label>
                  Actor type
                  <select name="actor_type" defaultValue="human">
                    <option value="human">human</option>
                    <option value="service">service</option>
                    <option value="automation">automation</option>
                  </select>
                </label>
                <TeamSelect name="team_id" teams={data.teams.items} defaultTeamId={context.teamId} />
                <label>Role refs<input name="role_refs" placeholder="role:member" /></label>
                <label>Policy refs<input name="policy_refs" placeholder="policy:project-read" /></label>
                <button className="primary" disabled={busy !== null}>
                  {busy === "membership.add" ? "Adding…" : "Add member"}
                </button>
              </form>
            </Card>
          </div>

          <Card title="Teams">
            <TeamTable
              teams={data.teams.items}
              selectedTeamId={context.teamId}
              onSelect={(teamId) => setContext((current) => ({ ...current, teamId }))}
            />
          </Card>

          <Card title={context.teamId ? "Team members" : "Organization members"}>
            <MembershipTable
              memberships={scopedMemberships}
              teams={data.teams.items}
              busy={busy}
              onAssign={(membership, roles, policies) =>
                void mutate(`membership.assign:${membership.id}`, () =>
                  client.assignMembership(membership.id, {
                    role_refs: roles,
                    policy_refs: policies,
                  }),
                )
              }
              onSuspend={(membership) =>
                void mutate(`membership.suspend:${membership.id}`, () =>
                  client.suspendMembership(membership.id),
                )
              }
              onRemove={(membership) =>
                void mutate(`membership.remove:${membership.id}`, () =>
                  client.removeMembership(membership.id),
                )
              }
            />
          </Card>

          <Card title="Invite collaborator">
            <form className="form-grid" onSubmit={inviteMember}>
              <label>Identity ref<input name="intended_identity_ref" placeholder="user:alice" /></label>
              <label>Email ref<input name="intended_email_ref" placeholder="alice@example.invalid" /></label>
              <TeamSelect name="team_id" teams={data.teams.items} defaultTeamId={context.teamId} />
              <label>Role refs<input name="role_refs" placeholder="role:member" /></label>
              <label>Policy refs<input name="policy_refs" placeholder="policy:project-read" /></label>
              <label>
                Expires in hours
                <input name="expires_in_hours" type="number" min="1" max="720" defaultValue="72" />
              </label>
              <button className="primary" disabled={busy !== null}>
                {busy === "invitation.create" ? "Creating…" : "Create invitation"}
              </button>
            </form>
          </Card>

          <Card title="Invitations">
            <InvitationTable
              invitations={data.invitations.items}
              teams={data.teams.items}
              busy={busy}
              onAccept={(invitation) =>
                void mutate(`invitation.accept:${invitation.id}`, () => client.acceptInvitation(invitation.id))
              }
              onRevoke={(invitation) =>
                void mutate(`invitation.revoke:${invitation.id}`, () => client.revokeInvitation(invitation.id))
              }
            />
          </Card>

          <OrganizationResourcesPanel
            client={client}
            organizationId={selectedOrganization.id}
            teams={data.teams.items}
          />

          <Card title="Membership & collaboration history">
            {data.audit ? <AuditTable events={data.audit.items} /> : (
              <EmptyState
                title="Audit history unavailable"
                detail="The organization service is available, but no audit EventProvider is configured."
              />
            )}
          </Card>
        </>
      )}
    </div>
  );
}

function OrganizationSummary({
  organization,
  team,
  onArchive,
  archiving,
}: {
  organization: CanonicalOrganization;
  team: CanonicalCollaborationTeam | null;
  onArchive: () => void;
  archiving: boolean;
}) {
  return (
    <div className="metrics">
      <div className="metric"><span>Organization</span><strong>{organization.display_name || organization.name}</strong></div>
      <div className="metric"><span>Status</span><strong><StatusBadge value={organization.status} /></strong></div>
      <div className="metric"><span>Team context</span><strong>{team?.name ?? "Organization-wide"}</strong></div>
      <div className="metric metric-action">
        <span>Lifecycle</span>
        <button
          className="danger"
          disabled={archiving || organization.status === "archived"}
          onClick={onArchive}
        >
          {archiving ? "Archiving…" : "Archive organization"}
        </button>
      </div>
    </div>
  );
}

function TeamSelect({
  name,
  teams,
  defaultTeamId,
}: {
  name: string;
  teams: CanonicalCollaborationTeam[];
  defaultTeamId: string | null;
}) {
  return (
    <label>
      Team
      <select name={name} defaultValue={defaultTeamId ?? ""}>
        <option value="">Organization-wide</option>
        {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
      </select>
    </label>
  );
}

function TeamTable({
  teams,
  selectedTeamId,
  onSelect,
}: {
  teams: CanonicalCollaborationTeam[];
  selectedTeamId: string | null;
  onSelect: (teamId: string | null) => void;
}) {
  if (!teams.length) return <EmptyState title="No teams" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Team</th><th>Status</th><th>Parent</th><th>Context</th></tr></thead>
        <tbody>
          {teams.map((team) => (
            <tr key={team.id}>
              <td><strong>{team.name}</strong><div><CanonicalId value={team.id} /></div></td>
              <td><StatusBadge value={team.status} /></td>
              <td>{team.parent_team_id ? <CanonicalId value={team.parent_team_id} /> : "—"}</td>
              <td>
                <button
                  className={selectedTeamId === team.id ? "secondary active" : "secondary"}
                  onClick={() => onSelect(selectedTeamId === team.id ? null : team.id)}
                >
                  {selectedTeamId === team.id ? "Organization scope" : "Use team"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MembershipTable({
  memberships,
  teams,
  busy,
  onAssign,
  onSuspend,
  onRemove,
}: {
  memberships: CanonicalMembership[];
  teams: CanonicalCollaborationTeam[];
  busy: string | null;
  onAssign: (membership: CanonicalMembership, roles: string[], policies: string[]) => void;
  onSuspend: (membership: CanonicalMembership) => void;
  onRemove: (membership: CanonicalMembership) => void;
}) {
  if (!memberships.length) return <EmptyState title="No members in this scope" />;
  const teamNames = new Map(teams.map((team) => [team.id, team.name]));
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Actor</th><th>Team</th><th>Status</th><th>Role / policy refs</th><th>Actions</th></tr></thead>
        <tbody>
          {memberships.map((membership) => (
            <MembershipRow
              key={membership.id}
              membership={membership}
              teamName={membership.team_id ? teamNames.get(membership.team_id) ?? membership.team_id : "Organization-wide"}
              busy={busy}
              onAssign={onAssign}
              onSuspend={onSuspend}
              onRemove={onRemove}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MembershipRow({
  membership,
  teamName,
  busy,
  onAssign,
  onSuspend,
  onRemove,
}: {
  membership: CanonicalMembership;
  teamName: string;
  busy: string | null;
  onAssign: (membership: CanonicalMembership, roles: string[], policies: string[]) => void;
  onSuspend: (membership: CanonicalMembership) => void;
  onRemove: (membership: CanonicalMembership) => void;
}) {
  const [roles, setRoles] = useState(membership.role_refs.join(", "));
  const [policies, setPolicies] = useState(membership.policy_refs.join(", "));
  useEffect(() => setRoles(membership.role_refs.join(", ")), [membership.role_refs]);
  useEffect(() => setPolicies(membership.policy_refs.join(", ")), [membership.policy_refs]);
  const active = membership.status === "active";
  return (
    <tr>
      <td><strong>{membership.actor_id}</strong><small>{membership.actor_type}</small><div><CanonicalId value={membership.id} /></div></td>
      <td>{teamName}</td>
      <td><StatusBadge value={membership.status} /></td>
      <td>
        <label className="compact-field">Roles<input aria-label={`Roles for ${membership.actor_id}`} value={roles} onChange={(event) => setRoles(event.target.value)} /></label>
        <label className="compact-field">Policies<input aria-label={`Policies for ${membership.actor_id}`} value={policies} onChange={(event) => setPolicies(event.target.value)} /></label>
      </td>
      <td className="actions-cell">
        <button
          className="secondary"
          disabled={!active || busy !== null}
          onClick={() => onAssign(membership, splitCsv(roles), splitCsv(policies))}
        >Save assignments</button>
        <button className="secondary" disabled={!active || busy !== null} onClick={() => onSuspend(membership)}>Suspend</button>
        <button className="danger" disabled={!active || busy !== null} onClick={() => onRemove(membership)}>Remove</button>
      </td>
    </tr>
  );
}

function InvitationTable({
  invitations,
  teams,
  busy,
  onAccept,
  onRevoke,
}: {
  invitations: CanonicalInvitation[];
  teams: CanonicalCollaborationTeam[];
  busy: string | null;
  onAccept: (invitation: CanonicalInvitation) => void;
  onRevoke: (invitation: CanonicalInvitation) => void;
}) {
  if (!invitations.length) return <EmptyState title="No invitations" />;
  const teamNames = new Map(teams.map((team) => [team.id, team.name]));
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Target</th><th>Team</th><th>Status</th><th>Expires</th><th>Actions</th></tr></thead>
        <tbody>
          {invitations.map((invitation) => (
            <tr key={invitation.id}>
              <td>{invitation.intended_identity_ref ?? invitation.intended_email_ref ?? "Unbound"}<div><CanonicalId value={invitation.id} /></div></td>
              <td>{invitation.team_id ? teamNames.get(invitation.team_id) ?? invitation.team_id : "Organization-wide"}</td>
              <td><StatusBadge value={invitation.status} /></td>
              <td>{formatDate(invitation.expires_at)}</td>
              <td className="actions-cell">
                <button className="secondary" disabled={invitation.status !== "pending" || busy !== null} onClick={() => onAccept(invitation)}>Accept as current user</button>
                <button className="danger" disabled={invitation.status !== "pending" || busy !== null} onClick={() => onRevoke(invitation)}>Revoke</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AuditTable({ events }: { events: CanonicalOrganizationAuditEvent[] }) {
  if (!events.length) return <EmptyState title="No collaboration history" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Affected</th><th>Status</th></tr></thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id}>
              <td>{formatDate(event.occurred_at)}</td>
              <td><strong>{event.event_type}</strong><div><CanonicalId value={event.id} /></div></td>
              <td>{event.actor_ref ?? "—"}</td>
              <td>{event.affected_actor_id ?? event.resource_id ?? event.resource_ref ?? "—"}</td>
              <td>{event.status ? <StatusBadge value={event.status} /> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function required(form: FormData, field: string): string {
  const value = String(form.get(field) ?? "").trim();
  if (!value) throw new Error(`${field} is required.`);
  return value;
}

function optional(form: FormData, field: string): string | undefined {
  const value = String(form.get(field) ?? "").trim();
  return value || undefined;
}

function csv(form: FormData, field: string): string[] {
  return splitCsv(String(form.get(field) ?? ""));
}

function splitCsv(value: string): string[] {
  return Array.from(new Set(value.split(",").map((item) => item.trim()).filter(Boolean)));
}

function expiresAt(form: FormData): string {
  const hours = Number(form.get("expires_in_hours") ?? 72);
  const duration = Number.isFinite(hours) && hours > 0 ? Math.min(hours, 720) : 72;
  return new Date(Date.now() + duration * 60 * 60 * 1000).toISOString();
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function loadContext(): CollaborationContext {
  try {
    const raw = localStorage.getItem(CONTEXT_KEY);
    if (!raw) return { organizationId: null, teamId: null };
    const parsed = JSON.parse(raw) as Partial<CollaborationContext>;
    return {
      organizationId: typeof parsed.organizationId === "string" ? parsed.organizationId : null,
      teamId: typeof parsed.teamId === "string" ? parsed.teamId : null,
    };
  } catch {
    return { organizationId: null, teamId: null };
  }
}

function persistContext(context: CollaborationContext): void {
  try {
    localStorage.setItem(CONTEXT_KEY, JSON.stringify(context));
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
}
