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
} from "../../api/organizations";
import { OrganizationClient } from "../../api/organizations";
import type { Page } from "../../api/types";
import { Card, EmptyState, ErrorState, LoadingState } from "../../components/States";
import { OrganizationConfigurationPanel } from "../OrganizationConfigurationPanel";
import { OrganizationResourcesPanel } from "../OrganizationResourcesPanel";
import {
  loadCollaborationContext,
  persistCollaborationContext,
  type CollaborationContext,
} from "./collaborationContext";
import { csv, expiresAt, optional, required } from "./organizationFormInput";
import {
  AuditTable,
  InvitationTable,
  MembershipTable,
  OrganizationSummary,
  TeamSelect,
  TeamTable,
} from "./OrganizationTables";

interface OrganizationData {
  teams: Page<CanonicalCollaborationTeam>;
  memberships: Page<CanonicalMembership>;
  invitations: Page<CanonicalInvitation>;
  audit: Page<CanonicalOrganizationAuditEvent> | null;
}

export function OrganizationsFeedback({
  error,
  mutationError,
  onRetry,
}: {
  error: unknown;
  mutationError: unknown;
  onRetry: () => void;
}) {
  return (
    <>
      {error ? <ErrorState error={error} onRetry={onRetry} /> : null}
      {mutationError ? <ErrorState error={mutationError} /> : null}
    </>
  );
}

export function PersonalOrganizationScope() {
  return (
    <Card title="Personal scope">
      <EmptyState
        title="No organization selected"
        detail="Personal work remains first-class. Select or create an organization only when collaboration is needed."
      />
    </Card>
  );
}

export function OrganizationsPage({ client }: { client: OrganizationClient }) {
  const [organizations, setOrganizations] = useState<Page<CanonicalOrganization> | null>(null);
  const [context, setContext] = useState<CollaborationContext>(() => loadCollaborationContext());
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
    persistCollaborationContext(context);
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

      <OrganizationsFeedback error={error} mutationError={mutationError} onRetry={() => void refresh()} />

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
                onChange={(event) => setContext({ organizationId: event.target.value || null, teamId: null })}
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
                onChange={(event) => setContext((current) => ({ ...current, teamId: event.target.value || null }))}
              >
                <option value="">Organization-wide</option>
                {data?.teams.items.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
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
          <label>Administrator actor refs<input name="administrator_actor_ids" placeholder="user:alice, service:ops" /></label>
          <button className="primary" disabled={busy !== null}>{busy === "organization.create" ? "Creating…" : "Create organization"}</button>
        </form>
      </Card>

      {!context.organizationId ? (
        <PersonalOrganizationScope />
      ) : !selectedOrganization || !data ? (
        <LoadingState />
      ) : (
        <>
          <OrganizationSummary
            organization={selectedOrganization}
            team={selectedTeam}
            onArchive={() => { if (window.confirm(`Archive organization ${selectedOrganization.id}?`)) void mutate("organization.archive", () => client.archiveOrganization(selectedOrganization.id)); }}
            archiving={busy === "organization.archive"}
          />

          <OrganizationConfigurationPanel client={client} organization={selectedOrganization} team={selectedTeam} teams={data.teams.items} onChanged={refresh} />

          <div className="two-column">
            <Card title="Create team">
              <form className="form-grid" onSubmit={createTeam}>
                <label>Name<input name="name" required /></label>
                <label>Description<input name="description" /></label>
                <label>
                  Parent team
                  <select name="parent_team_id" defaultValue="">
                    <option value="">None</option>
                    {data.teams.items.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
                  </select>
                </label>
                <button className="primary" disabled={busy !== null}>{busy === "team.create" ? "Creating…" : "Create team"}</button>
              </form>
            </Card>

            <Card title="Add member">
              <form className="form-grid" onSubmit={addMember}>
                <label>Actor ref<input name="actor_id" placeholder="user:alice" required /></label>
                <label>
                  Actor type
                  <select name="actor_type" defaultValue="human">
                    <option value="human">human</option><option value="service">service</option><option value="automation">automation</option>
                  </select>
                </label>
                <TeamSelect name="team_id" teams={data.teams.items} defaultTeamId={context.teamId} />
                <label>Role refs<input name="role_refs" placeholder="role:member" /></label>
                <label>Policy refs<input name="policy_refs" placeholder="policy:project-read" /></label>
                <button className="primary" disabled={busy !== null}>{busy === "membership.add" ? "Adding…" : "Add member"}</button>
              </form>
            </Card>
          </div>

          <Card title="Teams">
            <TeamTable teams={data.teams.items} selectedTeamId={context.teamId} onSelect={(teamId) => setContext((current) => ({ ...current, teamId }))} />
          </Card>

          <Card title={context.teamId ? "Team members" : "Organization members"}>
            <MembershipTable
              memberships={scopedMemberships}
              teams={data.teams.items}
              busy={busy}
              onAssign={(membership, roles, policies) => void mutate(`membership.assign:${membership.id}`, () => client.assignMembership(membership.id, { role_refs: roles, policy_refs: policies }))}
              onSuspend={(membership) => void mutate(`membership.suspend:${membership.id}`, () => client.suspendMembership(membership.id))}
              onRemove={(membership) => { if (window.confirm(`Remove membership ${membership.id}?`)) void mutate(`membership.remove:${membership.id}`, () => client.removeMembership(membership.id)); }}
            />
          </Card>

          <Card title="Invite collaborator">
            <form className="form-grid" onSubmit={inviteMember}>
              <label>Identity ref<input name="intended_identity_ref" placeholder="user:alice" /></label>
              <label>Email ref<input name="intended_email_ref" placeholder="alice@example.invalid" /></label>
              <TeamSelect name="team_id" teams={data.teams.items} defaultTeamId={context.teamId} />
              <label>Role refs<input name="role_refs" placeholder="role:member" /></label>
              <label>Policy refs<input name="policy_refs" placeholder="policy:project-read" /></label>
              <label>Expires in hours<input name="expires_in_hours" type="number" min="1" max="720" defaultValue="72" /></label>
              <button className="primary" disabled={busy !== null}>{busy === "invitation.create" ? "Creating…" : "Create invitation"}</button>
            </form>
          </Card>

          <Card title="Invitations">
            <InvitationTable
              invitations={data.invitations.items}
              teams={data.teams.items}
              busy={busy}
              onAccept={(invitation) => void mutate(`invitation.accept:${invitation.id}`, () => client.acceptInvitation(invitation.id))}
              onRevoke={(invitation) => { if (window.confirm(`Revoke invitation ${invitation.id}?`)) void mutate(`invitation.revoke:${invitation.id}`, () => client.revokeInvitation(invitation.id)); }}
            />
          </Card>

          <OrganizationResourcesPanel client={client} organizationId={selectedOrganization.id} teams={data.teams.items} />

          <Card title="Membership & collaboration history">
            {data.audit ? <AuditTable events={data.audit.items} /> : (
              <EmptyState title="Audit history unavailable" detail="The organization service is available, but no audit EventProvider is configured." />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
