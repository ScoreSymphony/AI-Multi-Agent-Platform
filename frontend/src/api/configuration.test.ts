import { describe, expect, it, vi } from "vitest";
import { emptyAgentProfile } from "../pages/AgentConfigurationPage";
import {
  ConfigurationClient,
  type CapabilityAssignmentContent,
} from "./configuration";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ConfigurationClient", () => {
  it("creates Agent definitions through the exact canonical command", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/agent.create");
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.has("idempotency-key")).toBe(true);
      expect(headers.has("x-correlation-id")).toBe(true);
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body.resource_ref).toBe("agents");
      expect(body.project_id).toBe("project_1");
      expect(body.profile).toBeTruthy();
      return jsonResponse({ id: "agent_1" });
    });
    const client = new ConfigurationClient({ fetchImpl });
    const profile = emptyAgentProfile();
    profile.name = "Reviewer";
    profile.role = "reviewer";

    await client.createAgent(profile, { project_id: "project_1" });

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("preserves optimistic Agent revision binding on update", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/agent.update");
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body.resource_ref).toBe("agent_1");
      expect(body.expected_revision).toBe(7);
      return jsonResponse({ id: "agent_1", current_revision: 8 });
    });
    const client = new ConfigurationClient({ fetchImpl });
    const profile = emptyAgentProfile();
    profile.name = "Reviewer";
    profile.role = "reviewer";

    await client.updateAgent("agent_1", profile, 7);
  });

  it("versions routing profiles instead of mutating immutable revisions", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/model-routing-profile.version");
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body.resource_ref).toBe("routing_profile_1");
      expect(body.expected_revision).toBe(3);
      return jsonResponse({ id: "routing_profile_1", current_revision: 4 });
    });
    const client = new ConfigurationClient({ fetchImpl });

    await client.versionRoutingProfile("routing_profile_1", 3, {
      name: "Local coding",
      description: "Prefer local tool-capable models",
      policy: {
        requirements: {
          explicit_model_id: null,
          min_context_window: 32768,
          tool_calling: true,
          structured_output: false,
          streaming: false,
          modalities: ["text"],
          reasoning: [],
          local_only: true,
          self_hosted_only: false,
        },
        preferred_model_ids: ["model_1"],
        fallback: "route",
      },
    });
  });

  it("never forwards client-authored provenance for Capability Assignments", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/capability-assignment.create");
      const body = JSON.parse(String(init?.body)) as {
        resource_ref: string;
        content: Record<string, unknown>;
      };
      expect(body.resource_ref).toBe("capability-assignments");
      expect(body.content.provenance).toBeUndefined();
      return jsonResponse({ id: "cap_assignment_1" });
    });
    const client = new ConfigurationClient({ fetchImpl });
    const content: CapabilityAssignmentContent = {
      target: { subject_type: "agent", subject_id: "agent_1" },
      required: [],
      allowed: [],
      denied: [],
      provenance: { source: "browser", creator_ref: "spoofed" },
      schema_version: "1.0",
    };

    await client.createCapabilityAssignment({ content });
  });

  it("binds Capability Assignment revisions to the loaded expected revision", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/capability-assignment.revise");
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body.resource_ref).toBe("cap_assignment_1");
      expect(body.expected_revision).toBe(2);
      return jsonResponse({ id: "cap_assignment_1", current_revision: 3 });
    });
    const client = new ConfigurationClient({ fetchImpl });

    await client.reviseCapabilityAssignment("cap_assignment_1", 2, {
      target: { subject_type: "agent", subject_id: "agent_1" },
      required: [],
      allowed: [],
      denied: [],
      schema_version: "1.0",
    });
  });
});
