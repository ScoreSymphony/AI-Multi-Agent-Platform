import { renderToStaticMarkup } from "react-dom/server";
import { ControlPlaneError } from "../../api/client";
import { describe, expect, it, vi } from "vitest";
import {
  OrganizationClient,
  type CanonicalCollaborationTeam,
  type CanonicalInvitation,
  type CanonicalMembership,
  type CanonicalOrganization,
} from "../../api/organizations";
import {
  InvitationTable,
  MembershipTable,
  OrganizationSummary,
  TeamTable,
} from "./OrganizationTables";
import {
  OrganizationsFeedback,
  OrganizationsPage,
  PersonalOrganizationScope,
} from "./OrganizationsPage";

function retryableBackendError(message: string): ControlPlaneError {
  return new ControlPlaneError(503, {
    code: "unavailable",
    category: "backend",
    message,
    request_id: "request_regression_retry",
    correlation_id: "correlation_regression_retry",
    retryable: true,
  });
}

function organization(overrides: Partial<CanonicalOrganization> = {}): CanonicalOrganization {
  return {
    id: "organization_123",
    type: "organization",
    name: "platform-lab",
    display_name: "Platform Lab",
    status: "active",
    owner_actor_id: "user:alice",
    administrator_actor_ids: ["user:alice"],
    settings: {},
    default_policy_refs: [],
    default_configuration_refs: [],
    provenance: {},
    created_at: "2026-09-16T00:00:00Z",
    updated_at: "2026-09-16T01:00:00Z",
    archived_at: null,
    ...overrides,
  };
}

function team(overrides: Partial<CanonicalCollaborationTeam> = {}): CanonicalCollaborationTeam {
  return {
    id: "team_123",
    type: "team",
    organization_id: "organization_123",
    name: "Verification",
    description: "Verification team",
    status: "active",
    parent_team_id: null,
    project_scope_refs: [],
    default_policy_refs: [],
    default_configuration_refs: [],
    created_at: "2026-09-16T00:00:00Z",
    updated_at: "2026-09-16T01:00:00Z",
    archived_at: null,
    ...overrides,
  };
}

function membership(overrides: Partial<CanonicalMembership> = {}): CanonicalMembership {
  return {
    id: "membership_123",
    type: "membership",
    actor_id: "user:alice",
    actor_type: "human",
    organization_id: "organization_123",
    team_id: "team_123",
    status: "active",
    role_refs: ["role:member"],
    policy_refs: ["policy:project-read"],
    created_by_actor_id: "user:owner",
    invited_by_actor_id: null,
    created_at: "2026-09-16T00:00:00Z",
    accepted_at: "2026-09-16T00:01:00Z",
    suspended_at: null,
    revoked_at: null,
    expires_at: null,
    ...overrides,
  };
}

function invitation(overrides: Partial<CanonicalInvitation> = {}): CanonicalInvitation {
  return {
    id: "invitation_123",
    type: "invitation",
    organization_id: "organization_123",
    team_id: "team_123",
    intended_identity_ref: "user:bob",
    intended_email_ref: null,
    invited_by_actor_id: "user:alice",
    requested_role_refs: ["role:member"],
    requested_policy_refs: [],
    status: "pending",
    created_at: "2026-09-16T00:00:00Z",
    expires_at: "2026-09-19T00:00:00Z",
    accepted_at: null,
    revoked_at: null,
    ...overrides,
  };
}

describe("Organizations page regression coverage", () => {
  it("preserves route composition, initial loading state and native create form semantics", () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("SSR regression coverage must not perform network requests");
    }) as unknown as typeof fetch;
    const html = renderToStaticMarkup(
      <OrganizationsPage client={new OrganizationClient({ fetchImpl })} />,
    );

    expect(html).toContain("<h1>Organizations &amp; teams</h1>");
    expect(html).toContain("Current collaboration context");
    expect(html).toContain("Loading…");
    expect(html).toContain("Create organization");
    expect(html).toContain('name="name"');
    expect(html).toContain("required");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("renders canonical backend and mutation errors without creating local domain state", () => {
    const retry = vi.fn();
    const html = renderToStaticMarkup(
      <OrganizationsFeedback
        error={retryableBackendError("organization service unavailable")}
        mutationError={new Error("membership update rejected")}
        onRetry={retry}
      />,
    );

    expect(html.match(/role="alert"/g)).toHaveLength(2);
    expect(html).toContain("organization service unavailable");
    expect(html).toContain("membership update rejected");
    expect(html).toContain(">Retry</button>");
  });

  it("keeps personal scope as the explicit empty collaboration state", () => {
    const html = renderToStaticMarkup(<PersonalOrganizationScope />);
    expect(html).toContain("Personal scope");
    expect(html).toContain("No organization selected");
    expect(html).toContain("Personal work remains first-class");
  });

  it("preserves organization lifecycle action availability", () => {
    const active = renderToStaticMarkup(
      <OrganizationSummary
        organization={organization()}
        team={team()}
        onArchive={vi.fn()}
        archiving={false}
      />,
    );
    expect(active).toContain("Platform Lab");
    expect(active).toContain("Verification");
    expect(active).toContain(">Archive organization</button>");
    expect(active).not.toContain("disabled");

    const archived = renderToStaticMarkup(
      <OrganizationSummary
        organization={organization({ status: "archived", archived_at: "2026-09-16T02:00:00Z" })}
        team={null}
        onArchive={vi.fn()}
        archiving={false}
      />,
    );
    expect(archived).toContain("Organization-wide");
    expect(archived).toContain("disabled");
  });

  it("uses native buttons for team scope selection and exposes selected-state behavior", () => {
    expect(renderToStaticMarkup(
      <TeamTable teams={[]} selectedTeamId={null} onSelect={vi.fn()} />,
    )).toContain("No teams");

    const unselected = renderToStaticMarkup(
      <TeamTable teams={[team()]} selectedTeamId={null} onSelect={vi.fn()} />,
    );
    expect(unselected).toContain(">Use team</button>");

    const selected = renderToStaticMarkup(
      <TeamTable teams={[team()]} selectedTeamId="team_123" onSelect={vi.fn()} />,
    );
    expect(selected).toContain("secondary active");
    expect(selected).toContain(">Organization scope</button>");
  });

  it("preserves labeled membership assignment controls and disables unavailable mutations", () => {
    const active = renderToStaticMarkup(
      <MembershipTable
        memberships={[membership()]}
        teams={[team()]}
        busy={null}
        onAssign={vi.fn()}
        onSuspend={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(active).toContain('aria-label="Roles for user:alice"');
    expect(active).toContain('aria-label="Policies for user:alice"');
    expect(active).toContain(">Save assignments</button>");
    expect(active).toContain(">Suspend</button>");
    expect(active).toContain(">Remove</button>");

    const suspended = renderToStaticMarkup(
      <MembershipTable
        memberships={[membership({ status: "suspended", suspended_at: "2026-09-16T02:00:00Z" })]}
        teams={[team()]}
        busy={null}
        onAssign={vi.fn()}
        onSuspend={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(suspended.match(/disabled/g)?.length).toBe(3);
  });

  it("keeps invitation acceptance unavailable without a bound identity while revoke stays available", () => {
    const html = renderToStaticMarkup(
      <InvitationTable
        invitations={[invitation({ intended_identity_ref: null, intended_email_ref: "bob@example.invalid" })]}
        teams={[team()]}
        busy={null}
        onAccept={vi.fn()}
        onRevoke={vi.fn()}
      />,
    );

    expect(html).toContain("bob@example.invalid");
    expect(html).toContain("<button class=\"secondary\" disabled=\"\">Accept as current user</button>");
    expect(html).toContain("<button class=\"danger\">Revoke</button>");
  });
});
