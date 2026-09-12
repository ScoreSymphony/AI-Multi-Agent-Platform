import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

export type OrganizationStatus = "active" | "archived";
export type MembershipStatus = "active" | "suspended" | "revoked" | "left";
export type InvitationStatus = "pending" | "accepted" | "expired" | "revoked";
export type ShareStatus = "active" | "revoked";
export type OrganizationActorType = "human" | "service" | "automation";
export type OrganizationOwnerType = "user" | "organization" | "team" | "service";

export interface CanonicalOrganization {
  id: string;
  type: "organization";
  name: string;
  display_name: string | null;
  status: OrganizationStatus;
  owner_actor_id: string;
  administrator_actor_ids: string[];
  settings: Record<string, JsonValue>;
  default_policy_refs: string[];
  default_configuration_refs: string[];
  provenance: Record<string, JsonValue>;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface CanonicalCollaborationTeam {
  id: string;
  type: "team";
  organization_id: string;
  name: string;
  description: string;
  status: "active" | "archived";
  parent_team_id: string | null;
  project_scope_refs: string[];
  default_policy_refs: string[];
  default_configuration_refs: string[];
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface CanonicalMembership {
  id: string;
  type: "membership";
  actor_id: string;
  actor_type: OrganizationActorType;
  organization_id: string;
  team_id: string | null;
  status: MembershipStatus;
  role_refs: string[];
  policy_refs: string[];
  created_by_actor_id: string | null;
  invited_by_actor_id: string | null;
  created_at: string;
  accepted_at: string;
  suspended_at: string | null;
  revoked_at: string | null;
  expires_at: string | null;
}

export interface CanonicalInvitation {
  id: string;
  type: "invitation";
  organization_id: string;
  team_id: string | null;
  intended_identity_ref: string | null;
  intended_email_ref: string | null;
  invited_by_actor_id: string;
  requested_role_refs: string[];
  requested_policy_refs: string[];
  status: InvitationStatus;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
}

export interface CanonicalResourceOwnership {
  id: string;
  type: "resource-ownership";
  resource_type: string;
  resource_id: string;
  owner_type: OrganizationOwnerType;
  owner_id: string;
  organization_id: string | null;
  created_by_actor_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CanonicalResourceShare {
  id: string;
  type: "resource-share";
  ownership_id: string;
  target_type: OrganizationOwnerType;
  target_id: string;
  granted_by_actor_id: string;
  organization_id: string | null;
  status: ShareStatus;
  policy_refs: string[];
  created_at: string;
  revoked_at: string | null;
}

export interface CanonicalOrganizationAuditEvent {
  id: string;
  type: "organization_audit_event";
  organization_id: string;
  event_type: string;
  occurred_at: string;
  command?: string;
  resource_ref?: string;
  resource_type?: string;
  resource_id?: string;
  team_id?: string;
  affected_actor_id?: string;
  owner_actor_id?: string;
  status?: string;
  role_refs?: string[];
  policy_refs?: string[];
  actor_ref?: string;
  request_id?: string;
}

export interface OwnerRefInput {
  type: OrganizationOwnerType;
  id: string;
}

export interface CreateOrganizationInput {
  name: string;
  display_name?: string;
  administrator_actor_ids?: string[];
}

export interface UpdateOrganizationInput {
  name?: string;
  display_name?: string | null;
  administrator_actor_ids?: string[];
  settings?: Record<string, JsonValue>;
  default_policy_refs?: string[];
  default_configuration_refs?: string[];
}

export interface TransferOrganizationOwnerInput {
  new_owner_actor_id: string;
}

export interface CreateTeamInput {
  name: string;
  description?: string;
  parent_team_id?: string;
}

export interface ConfigureTeamInput {
  name?: string;
  description?: string;
  parent_team_id?: string | null;
  project_scope_refs?: string[];
  default_policy_refs?: string[];
  default_configuration_refs?: string[];
}

export interface AddMembershipInput {
  actor_id: string;
  actor_type?: OrganizationActorType;
  team_id?: string;
  role_refs?: string[];
  policy_refs?: string[];
}

export interface MembershipAssignmentsInput {
  role_refs: string[];
  policy_refs: string[];
}

export interface CreateInvitationInput {
  intended_identity_ref?: string;
  intended_email_ref?: string;
  team_id?: string;
  role_refs?: string[];
  policy_refs?: string[];
  expires_at: string;
}

export interface SetOwnershipInput {
  resource_type: string;
  resource_id: string;
  owner_ref: OwnerRefInput;
  organization_id?: string;
}

export interface TransferOwnershipInput extends SetOwnershipInput {}

export interface CreateResourceShareInput {
  resource_type: string;
  resource_id: string;
  target_ref: OwnerRefInput;
  policy_refs?: string[];
  allow_cross_organization?: boolean;
}

export interface OrganizationClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export class OrganizationClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;

  constructor(options: OrganizationClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
  }

  listOrganizations(query: ListQuery = {}): Promise<Page<CanonicalOrganization>> {
    return this.list<CanonicalOrganization>("organizations", query);
  }

  createOrganization(input: CreateOrganizationInput): Promise<CanonicalOrganization> {
    return this.command("organization.create", "organizations", input);
  }

  updateOrganization(
    organizationId: string,
    input: UpdateOrganizationInput,
  ): Promise<CanonicalOrganization> {
    return this.command("organization.update", organizationId, input);
  }

  transferOrganizationOwner(
    organizationId: string,
    input: TransferOrganizationOwnerInput,
  ): Promise<CanonicalOrganization> {
    return this.command("organization.owner.transfer", organizationId, input);
  }

  archiveOrganization(organizationId: string): Promise<CanonicalOrganization> {
    return this.command("organization.archive", organizationId);
  }

  listTeams(organizationId: string): Promise<Page<CanonicalCollaborationTeam>> {
    return this.list<CanonicalCollaborationTeam>("teams", {
      limit: 250,
      filters: { organization_id: organizationId },
    });
  }

  createTeam(
    organizationId: string,
    input: CreateTeamInput,
  ): Promise<CanonicalCollaborationTeam> {
    return this.command("team.create", organizationId, input);
  }

  updateTeam(teamId: string, input: CreateTeamInput): Promise<CanonicalCollaborationTeam> {
    return this.command("team.update", teamId, input);
  }

  configureTeam(teamId: string, input: ConfigureTeamInput): Promise<CanonicalCollaborationTeam> {
    return this.command("team.configure", teamId, input);
  }

  listMemberships(organizationId: string): Promise<Page<CanonicalMembership>> {
    return this.list<CanonicalMembership>("memberships", {
      limit: 500,
      filters: { organization_id: organizationId },
    });
  }

  addMembership(
    organizationId: string,
    input: AddMembershipInput,
  ): Promise<CanonicalMembership> {
    return this.command("membership.add", organizationId, input);
  }

  assignMembership(
    membershipId: string,
    input: MembershipAssignmentsInput,
  ): Promise<CanonicalMembership> {
    return this.command("membership.assign", membershipId, input);
  }

  suspendMembership(membershipId: string): Promise<CanonicalMembership> {
    return this.command("membership.suspend", membershipId);
  }

  removeMembership(membershipId: string): Promise<CanonicalMembership> {
    return this.command("membership.remove", membershipId);
  }

  leaveMembership(membershipId: string): Promise<CanonicalMembership> {
    return this.command("membership.leave", membershipId);
  }

  listInvitations(organizationId: string): Promise<Page<CanonicalInvitation>> {
    return this.list<CanonicalInvitation>("invitations", {
      limit: 250,
      filters: { organization_id: organizationId },
    });
  }

  createInvitation(
    organizationId: string,
    input: CreateInvitationInput,
  ): Promise<CanonicalInvitation> {
    return this.command("invitation.create", organizationId, input);
  }

  revokeInvitation(invitationId: string): Promise<CanonicalInvitation> {
    return this.command("invitation.revoke", invitationId);
  }

  acceptInvitation(invitationId: string): Promise<CanonicalMembership> {
    return this.command("invitation.accept", invitationId);
  }

  listOwnerships(organizationId: string): Promise<Page<CanonicalResourceOwnership>> {
    return this.list<CanonicalResourceOwnership>("resource-ownerships", {
      limit: 500,
      filters: { organization_id: organizationId },
    });
  }

  listShares(organizationId: string): Promise<Page<CanonicalResourceShare>> {
    return this.list<CanonicalResourceShare>("resource-shares", {
      limit: 500,
      filters: { organization_id: organizationId },
    });
  }

  setOwnership(input: SetOwnershipInput): Promise<CanonicalResourceOwnership> {
    return this.command("resource-ownership.set", input.resource_id, input);
  }

  transferOwnership(input: TransferOwnershipInput): Promise<CanonicalResourceOwnership> {
    return this.command("resource-ownership.transfer", input.resource_id, input);
  }

  createShare(input: CreateResourceShareInput): Promise<CanonicalResourceShare> {
    return this.command("resource-share.create", input.resource_id, input);
  }

  revokeShare(shareId: string): Promise<CanonicalResourceShare> {
    return this.command("resource-share.revoke", shareId);
  }

  listAudit(organizationId: string): Promise<Page<CanonicalOrganizationAuditEvent>> {
    return this.list<CanonicalOrganizationAuditEvent>("organization-audit-events", {
      limit: 500,
      sort: "occurred_at",
      direction: "desc",
      filters: { organization_id: organizationId },
    });
  }

  private list<T>(collection: string, query: ListQuery): Promise<Page<T>> {
    return this.transport.request<Page<T>>(`/${collection}${toQuery(query)}`);
  }

  private command<T>(command: string, resourceRef: string, payload: object = {}): Promise<T> {
    return this.transport.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      body: { resource_ref: resourceRef, ...payload },
      idempotencyKey: crypto.randomUUID(),
    });
  }
}

function toQuery(query: ListQuery): string {
  const params = new URLSearchParams();
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.sort) params.set("sort", query.sort);
  if (query.direction) params.set("direction", query.direction);
  if (query.q) params.set("q", query.q);
  for (const [field, value] of Object.entries(query.filters ?? {})) {
    params.set(`filter[${field}]`, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}
