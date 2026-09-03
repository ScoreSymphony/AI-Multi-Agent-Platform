import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";

const emptyPage = { items: [], next_cursor: null, total: 0, limit: 100 };

function clientWithFetch() {
  const fetchSpy = vi.fn().mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(emptyPage), { status: 200 })),
  );
  return {
    client: new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch }),
    fetchSpy,
  };
}

function requestPath(fetchSpy: ReturnType<typeof vi.fn>): URL {
  const [url, init] = fetchSpy.mock.calls.at(-1) as [string, RequestInit];
  expect(init.method).toBe("GET");
  return new URL(url, "https://platform.invalid");
}

describe("#17 Agent and Capability canonical clients", () => {
  it("keeps Agent, Team and AgentRun inventory under /api/v1", async () => {
    const { client, fetchSpy } = clientWithFetch();

    await client.listAgents({ cursor: "agent/cursor+1==" });
    expect(requestPath(fetchSpy).pathname).toBe("/api/v1/agents");
    expect(requestPath(fetchSpy).searchParams.get("cursor")).toBe("agent/cursor+1==");

    await client.listAgentTeams({ cursor: "team/cursor+1==" });
    expect(requestPath(fetchSpy).pathname).toBe("/api/v1/agent-teams");

    await client.listAgentRuns({ cursor: "run/cursor+1==" });
    expect(requestPath(fetchSpy).pathname).toBe("/api/v1/agent-runs");
  });

  it("keeps Capability inventory and providers under /api/v1", async () => {
    const { client, fetchSpy } = clientWithFetch();

    await client.listCapabilities({ cursor: "cap/cursor+1==" });
    expect(requestPath(fetchSpy).pathname).toBe("/api/v1/capabilities");
    expect(requestPath(fetchSpy).searchParams.get("cursor")).toBe("cap/cursor+1==");

    await client.listCapabilityProviders({ cursor: "provider/cursor+1==" });
    expect(requestPath(fetchSpy).pathname).toBe("/api/v1/capability-providers");
  });
});
