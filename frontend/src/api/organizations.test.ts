import { describe, expect, it, vi } from "vitest";
import { OrganizationClient } from "./organizations";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("OrganizationClient", () => {
  it("reads organization collections through canonical scope-aware routes", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push(String(input));
      expect(init?.credentials).toBe("include");
      expect(init?.method ?? "GET").toBe("GET");
      return jsonResponse({ items: [], total: 0, next_cursor: null, previous_cursor: null });
    });
    const client = new OrganizationClient({ fetchImpl });

    await client.listOrganizations();
    await client.listTeams("org_1");
    await client.listMemberships("org_1");
    await client.listInvitations("org_1");
    await client.listOwnerships("org_1");
    await client.listShares("org_1");
    await client.listAudit("org_1");

    expect(calls[0]).toContain("/api/v1/organizations");
    expect(calls[1]).toContain("/api/v1/teams");
    expect(calls[1]).toContain("filter%5Borganization_id%5D=org_1");
    expect(calls[2]).toContain("/api/v1/memberships");
    expect(calls[3]).toContain("/api/v1/invitations");
    expect(calls[4]).toContain("/api/v1/resource-ownerships");
    expect(calls[5]).toContain("/api/v1/resource-shares");
    expect(calls[6]).toContain("/api/v1/organization-audit-events");
  });

  it("uses exact organization commands and idempotency headers", async () => {
    const calls: Array<{ url: string; headers: Headers; body: Record<string, unknown> }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        headers: new Headers(init?.headers),
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      });
      return jsonResponse({ id: "resource_1" });
    });
    const client = new OrganizationClient({ fetchImpl });

    await client.createOrganization({ name: "Example" });
    await client.updateOrganization("org_1", {
      display_name: "Example Org",
      administrator_actor_ids: ["user:admin"],
    });
    await client.transferOrganizationOwner("org_1", {
      new_owner_actor_id: "user:member",
    });
    await client.createTeam("org_1", { name: "Platform" });
    await client.configureTeam("team_1", {
      description: "Platform team",
      project_scope_refs: ["project_scope:alpha"],
    });
    await client.addMembership("org_1", {
      actor_id: "user:member",
      actor_type: "human",
      role_refs: ["role:member"],
      policy_refs: ["policy:read"],
    });
    await client.assignMembership("membership_1", {
      role_refs: ["role:reviewer"],
      policy_refs: ["policy:review"],
    });
    await client.suspendMembership("membership_1");
    await client.removeMembership("membership_1");
    await client.leaveMembership("membership_2");
    await client.createInvitation("org_1", {
      intended_identity_ref: "user:invitee",
      expires_at: "2026-09-04T00:00:00+00:00",
      token_ref: "secret:invitation:test",
    });
    await client.revokeInvitation("invitation_1");
    await client.setOwnership({
      resource_type: "template",
      resource_id: "template_1",
      owner_ref: { type: "organization", id: "org_1" },
      organization_id: "org_1",
    });
    await client.transferOwnership({
      resource_type: "template",
      resource_id: "template_1",
      owner_ref: { type: "team", id: "team_1" },
      organization_id: "org_1",
    });
    await client.createShare({
      resource_type: "template",
      resource_id: "template_1",
      target_ref: { type: "team", id: "team_2" },
      policy_refs: ["policy:read"],
      allow_cross_organization: true,
    });
    await client.revokeShare("share_1");

    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/commands/organization.create",
      "/api/v1/commands/organization.update",
      "/api/v1/commands/organization.owner.transfer",
      "/api/v1/commands/team.create",
      "/api/v1/commands/team.configure",
      "/api/v1/commands/membership.add",
      "/api/v1/commands/membership.assign",
      "/api/v1/commands/membership.suspend",
      "/api/v1/commands/membership.remove",
      "/api/v1/commands/membership.leave",
      "/api/v1/commands/invitation.create",
      "/api/v1/commands/invitation.revoke",
      "/api/v1/commands/resource-ownership.set",
      "/api/v1/commands/resource-ownership.transfer",
      "/api/v1/commands/resource-share.create",
      "/api/v1/commands/resource-share.revoke",
    ]);
    for (const call of calls) {
      expect(call.headers.has("idempotency-key")).toBe(true);
      expect(call.headers.has("x-correlation-id")).toBe(true);
    }
    expect(calls[0]?.body).toMatchObject({ resource_ref: "organizations", name: "Example" });
    expect(calls[1]?.body).toMatchObject({
      resource_ref: "org_1",
      display_name: "Example Org",
    });
    expect(calls[2]?.body).toMatchObject({
      resource_ref: "org_1",
      new_owner_actor_id: "user:member",
    });
    expect(calls[5]?.body).toMatchObject({
      resource_ref: "org_1",
      actor_id: "user:member",
    });
    expect(calls[10]?.body.token_ref).toBe("secret:invitation:test");
    expect(calls[12]?.body).toMatchObject({
      resource_ref: "template_1",
      resource_id: "template_1",
      resource_type: "template",
    });
    expect(calls[14]?.body).toMatchObject({
      target_ref: { type: "team", id: "team_2" },
      allow_cross_organization: true,
    });
  });

  it("never adds token material to read requests", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).not.toContain("token");
      expect(init?.body).toBeUndefined();
      return jsonResponse({
        items: [
          {
            id: "invitation_1",
            type: "invitation",
            organization_id: "org_1",
            status: "pending",
          },
        ],
        total: 1,
        next_cursor: null,
        previous_cursor: null,
      });
    });
    const client = new OrganizationClient({ fetchImpl });

    await client.listInvitations("org_1");
    expect(fetchImpl).toHaveBeenCalledOnce();
  });
});
