import { useEffect, useState } from "react";
import type {
  CanonicalCollaborationTeam,
  CanonicalInvitation,
  CanonicalMembership,
  CanonicalOrganization,
  CanonicalOrganizationAuditEvent,
} from "../../api/organizations";
import { CanonicalId, EmptyState, StatusBadge } from "../../components/States";
import { splitCsv } from "./organizationFormInput";

export function OrganizationSummary({
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

export function TeamSelect({
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

export function TeamTable({
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

export function MembershipTable({
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

export function InvitationTable({
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
                <button className="secondary" disabled={invitation.status !== "pending" || invitation.intended_identity_ref === null || busy !== null} onClick={() => onAccept(invitation)}>Accept as current user</button>
                <button className="danger" disabled={invitation.status !== "pending" || busy !== null} onClick={() => onRevoke(invitation)}>Revoke</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AuditTable({ events }: { events: CanonicalOrganizationAuditEvent[] }) {
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

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
