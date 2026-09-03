import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";

const projectId = "project_123e4567-e89b-42d3-a456-426614174001";
const workspaceId = "workspace_123e4567-e89b-42d3-a456-426614174002";

describe("canonical Workspace client", () => {
  it("sends #37 Workspace creation options through the versioned Control Plane", async () => {
    const workspace = {
      id: workspaceId,
      type: "workspace",
      project_id: projectId,
      owner: { type: "user", id: "test" },
      lifecycle: "canonical",
      workspace_type: "isolated_run",
      status: "active",
      access_mode: "read_write",
      retention: "ephemeral",
      revision: 0,
      base_snapshot_id: null,
      source_refs: [],
      policy_labels: [],
      active_task_ids: [],
      active_run_ids: [],
      created_at: "2026-09-03T02:00:00+00:00",
      updated_at: "2026-09-03T02:00:00+00:00",
      last_used_at: "2026-09-03T02:00:00+00:00",
      expires_at: null,
    };
    const fetchSpy = vi.fn().mockResolvedValue(new Response(JSON.stringify(workspace), { status: 201 }));
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const created = await client.createWorkspace({
      project_id: projectId,
      workspace_type: "isolated_run",
      access_mode: "read_write",
      retention: "ephemeral",
    });

    expect(created.lifecycle).toBe("canonical");
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/workspaces");
    expect(init.method).toBe("POST");
    expect((init.headers as Headers).get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({
      project_id: projectId,
      workspace_type: "isolated_run",
      access_mode: "read_write",
      retention: "ephemeral",
    });
  });

  it("keeps the provider-absent identity fallback on the same canonical route", async () => {
    const fallback = {
      id: workspaceId,
      type: "workspace",
      project_id: projectId,
      owner: { type: "user", id: "test" },
      created_at: null,
      lifecycle: "identity_only",
    };
    const fetchSpy = vi.fn().mockResolvedValue(new Response(JSON.stringify(fallback), { status: 200 }));
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const workspace = await client.getWorkspace(workspaceId);

    expect(workspace.lifecycle).toBe("identity_only");
    expect((fetchSpy.mock.calls[0] as [string, RequestInit])[0]).toBe(`/api/v1/workspaces/${workspaceId}`);
  });
});
