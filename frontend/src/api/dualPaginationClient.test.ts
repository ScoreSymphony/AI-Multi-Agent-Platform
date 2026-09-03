import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";

const emptyPage = { items: [], next_cursor: null, total: 0, limit: 100 };

function makeClient() {
  const fetchSpy = vi.fn().mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(emptyPage), { status: 200 })),
  );
  return {
    client: new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch }),
    fetchSpy,
  };
}

function parsedCall(fetchSpy: ReturnType<typeof vi.fn>, index: number): URL {
  const [url] = fetchSpy.mock.calls[index] as [string, RequestInit];
  return new URL(url, "https://platform.invalid");
}

describe("independent canonical inventory cursors", () => {
  it("keeps Project and Workspace cursors on separate collections", async () => {
    const { client, fetchSpy } = makeClient();

    await client.listProjects({ limit: 100, cursor: "project-cursor", sort: "updated_at", direction: "desc" });
    await client.listWorkspaces({ limit: 100, cursor: "workspace-cursor", sort: "created_at", direction: "desc" });

    const projects = parsedCall(fetchSpy, 0);
    const workspaces = parsedCall(fetchSpy, 1);
    expect(projects.pathname).toBe("/api/v1/projects");
    expect(projects.searchParams.get("cursor")).toBe("project-cursor");
    expect(workspaces.pathname).toBe("/api/v1/workspaces");
    expect(workspaces.searchParams.get("cursor")).toBe("workspace-cursor");
  });

  it("keeps Model and Provider cursors on separate collections", async () => {
    const { client, fetchSpy } = makeClient();

    await client.listModels({ limit: 100, cursor: "model-cursor", sort: "display_name", direction: "asc" });
    await client.listModelProviders({ limit: 100, cursor: "provider-cursor", sort: "id", direction: "asc" });

    const models = parsedCall(fetchSpy, 0);
    const providers = parsedCall(fetchSpy, 1);
    expect(models.pathname).toBe("/api/v1/models");
    expect(models.searchParams.get("cursor")).toBe("model-cursor");
    expect(providers.pathname).toBe("/api/v1/model-providers");
    expect(providers.searchParams.get("cursor")).toBe("provider-cursor");
  });
});