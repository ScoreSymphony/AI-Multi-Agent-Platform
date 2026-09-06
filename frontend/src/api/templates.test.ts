import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient } from "./browserSession";
import { TemplateClient, emptyTemplateContent } from "./templates";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("TemplateClient", () => {
  it("reads Templates and instances through canonical collections", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 50 });
    });
    const client = new TemplateClient({ fetchImpl });

    await client.listTemplates({ limit: 20, cursor: "template-cursor" });
    await client.listInstances({ limit: 30, cursor: "instance-cursor" });

    expect(calls).toEqual([
      "/api/v1/templates?limit=20&cursor=template-cursor",
      "/api/v1/template-instances?limit=30&cursor=instance-cursor",
    ]);
  });

  it("creates canonical Template drafts without client-owned environment claims", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/template.create");
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const body = JSON.parse(String(init?.body));
      expect(body.resource_ref).toBe("templates");
      expect(body.content.name).toBe("New Template");
      expect(body.environment).toBeUndefined();
      expect(body.capability_ids).toBeUndefined();
      return jsonResponse({ id: "template-created" });
    });
    const client = new TemplateClient({ fetchImpl });

    await client.create(emptyTemplateContent(), {}, "template-create-key");
  });

  it("uses the shared BrowserSession CSRF boundary plus Template idempotency", async () => {
    const csrfStorage = new Map<string, string>([
      ["ai-agent-platform.csrf-token", "csrf-template-test"],
    ]);
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/template.create");
      const headers = new Headers(init?.headers);
      expect(headers.get("X-CSRF-Token")).toBe("csrf-template-test");
      expect(headers.get("Idempotency-Key")).toBe("template-browser-key");
      expect(init?.credentials).toBe("include");
      return jsonResponse({ id: "template-created" });
    });
    const session = new BrowserSessionClient({
      fetchImpl,
      storage: {
        getItem: (key) => csrfStorage.get(key) ?? null,
        setItem: (key, value) => void csrfStorage.set(key, value),
        removeItem: (key) => void csrfStorage.delete(key),
      },
    });
    const client = new TemplateClient({ fetchImpl: session.fetch });

    await client.create(emptyTemplateContent(), {}, "template-browser-key");
  });

  it("uses the exact canonical create-from-existing commands", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: JSON.parse(String(init?.body)) });
      return jsonResponse({ id: "template-created" });
    });
    const client = new TemplateClient({ fetchImpl });

    await client.createFromAgent("agent-1", { revision: 2, name: "Agent template" }, "agent-key");
    await client.createFromAgentTeam("team-1", {}, "team-key");
    await client.createFromWorkflow(
      "workflow-1",
      { revision: 2, name: "Workflow template" },
      "workflow-key",
    );
    await client.createFromCapabilityAssignment(
      "cap_assignment-1",
      { revision: 3, name: "Capability policy" },
      "capability-assignment-key",
    );
    await client.createFromModelRoutingProfile(
      "model_routing_profile-1",
      { revision: 4, name: "Routing policy" },
      "routing-profile-key",
    );
    await client.createFromAutomation("automation-1", {}, "automation-key");
    await client.createFromProject("project-1", {}, "project-key");
    await client.createFromWorkspaces(
      ["workspace-1", "workspace-2"],
      {
        name: "Workspace setup",
        project_template_id: "template-project",
        project_template_revision: 4,
      },
      "workspace-key",
    );

    expect(calls.map((item) => item.url)).toEqual([
      "/api/v1/commands/template.create-from-agent",
      "/api/v1/commands/template.create-from-agent-team",
      "/api/v1/commands/template.create-from-workflow",
      "/api/v1/commands/template.create-from-capability-assignment",
      "/api/v1/commands/template.create-from-model-routing-profile",
      "/api/v1/commands/template.create-from-automation",
      "/api/v1/commands/template.create-from-project",
      "/api/v1/commands/template.create-from-workspaces",
    ]);
    expect(calls[2]?.body).toEqual({
      resource_ref: "templates",
      workflow_id: "workflow-1",
      revision: 2,
      name: "Workflow template",
    });
    expect(calls[3]?.body).toEqual({
      resource_ref: "templates",
      assignment_id: "cap_assignment-1",
      revision: 3,
      name: "Capability policy",
    });
    expect(calls[4]?.body).toEqual({
      resource_ref: "templates",
      profile_id: "model_routing_profile-1",
      revision: 4,
      name: "Routing policy",
    });
    expect(calls[7]?.body).toEqual({
      resource_ref: "templates",
      workspace_ids: ["workspace-1", "workspace-2"],
      name: "Workspace setup",
      project_template_id: "template-project",
      project_template_revision: 4,
    });
  });

  it("previews then applies a pinned Template revision", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      calls.push({ url: String(input), body });
      if (String(input).endsWith("template.preview")) {
        return jsonResponse({ source: { template_id: "template-1", revision: 3 }, applicable: true });
      }
      return jsonResponse({ id: "template-instance-1" });
    });
    const client = new TemplateClient({ fetchImpl });

    await client.preview("template-1", { revision: 3 }, "preview-key");
    await client.apply("template-1", 3, "apply-key");

    expect(calls).toEqual([
      {
        url: "/api/v1/commands/template.preview",
        body: { resource_ref: "template-1", revision: 3 },
      },
      {
        url: "/api/v1/commands/template.apply",
        body: { resource_ref: "template-1", revision: 3 },
      },
    ]);
  });

  it("activates an untrusted Template through the canonical publish command", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/template.publish");
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "template-untrusted",
        expected_revision: 4,
        activate_untrusted: true,
      });
      const headers = new Headers(init?.headers);
      expect(headers.get("Idempotency-Key")).toBe("activate-template-key");
      return jsonResponse({ id: "template-untrusted", current_revision: 5 });
    });
    const client = new TemplateClient({ fetchImpl });

    await client.activateUntrusted("template-untrusted", 4, "activate-template-key");
  });
});
