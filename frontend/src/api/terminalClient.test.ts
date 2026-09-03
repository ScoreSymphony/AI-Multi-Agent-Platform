import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";
import { buildTerminalStreamUrl, parseTerminalStreamMessage } from "./terminal";

const session = {
  id: "terminal_session_123e4567-e89b-42d3-a456-426614174000",
  session_type: "manual",
  project_id: "project_123e4567-e89b-42d3-a456-426614174001",
  workspace_id: "workspace_123e4567-e89b-42d3-a456-426614174002",
  context: {
    project_id: "project_123e4567-e89b-42d3-a456-426614174001",
    workspace_id: "workspace_123e4567-e89b-42d3-a456-426614174002",
    task_id: null,
    run_id: null,
    worker_id: null,
    node_id: null,
  },
  mode: "interactive",
  owner_actor_ref: "user:test",
  adapter_id: "reference-terminal",
  capabilities: {
    interactive_input: true,
    resize: false,
    reconnect: true,
    terminate: true,
    pty: false,
  },
  status: "running",
  started_at: "2026-09-03T00:00:00+00:00",
  ended_at: null,
  encoding: "utf-8",
  dimensions: null,
  policy_classification: [],
  inactivity_timeout_seconds: 600,
  retain_transcript: false,
  diagnostics: [],
};

describe("terminal Control Plane client", () => {
  it("lists terminal sessions only through the canonical extension collection", async () => {
    const page = { items: [session], next_cursor: null, total: 1, limit: 100 };
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(page), { status: 200 }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.listTerminalSessions({
      limit: 100,
      filters: { workspace_id: session.context.workspace_id, status: "running" },
    });

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/terminal-sessions?");
    expect(url).toContain("filter%5Bworkspace_id%5D=");
    expect(url).toContain("filter%5Bstatus%5D=running");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");
  });

  it("creates and terminates sessions through idempotent canonical commands", async () => {
    const fetchSpy = vi.fn().mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify(session), { status: 200 })),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.createTerminalSession(session.context.project_id, {
      workspace_id: session.context.workspace_id,
      session_type: "manual",
      mode: "interactive",
      inactivity_timeout_seconds: 600,
      retain_transcript: false,
    });
    await client.terminateTerminalSession(session.id, "operator requested");

    const [createUrl, createInit] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const [terminateUrl, terminateInit] = fetchSpy.mock.calls[1] as [string, RequestInit];
    expect(createUrl).toBe("/api/v1/commands/terminal.session.create");
    expect(JSON.parse(String(createInit.body))).toMatchObject({
      resource_ref: session.context.project_id,
      workspace_id: session.context.workspace_id,
      session_type: "manual",
      mode: "interactive",
      inactivity_timeout_seconds: 600,
      retain_transcript: false,
    });
    expect(terminateUrl).toBe("/api/v1/commands/terminal.session.terminate");
    expect(JSON.parse(String(terminateInit.body))).toMatchObject({
      resource_ref: session.id,
      reason: "operator requested",
    });
    expect((createInit.headers as Headers).get("Idempotency-Key")).toBeTruthy();
    expect((terminateInit.headers as Headers).get("Idempotency-Key")).toBeTruthy();
  });

  it("brokers browser streaming through the versioned Control Plane gateway", () => {
    expect(
      buildTerminalStreamUrl(
        "https://control.example",
        session.id,
        12,
        "https://ui.example",
      ),
    ).toBe(
      `wss://control.example/api/v1/terminal-sessions/${session.id}/stream?after_sequence=12`,
    );
  });

  it("accepts canonical frames and rejects unknown stream envelopes", () => {
    const frame = {
      id: "terminal_frame_123e4567-e89b-42d3-a456-426614174003",
      session_id: session.id,
      sequence: 1,
      channel: "stdout",
      data: "hello\n",
      occurred_at: "2026-09-03T00:00:00+00:00",
      final: false,
    };
    expect(parseTerminalStreamMessage({ type: "stream.frame", frame })).toMatchObject({
      type: "stream.frame",
      frame,
    });
    expect(parseTerminalStreamMessage({ type: "private-pty-handle", handle: "/dev/pts/1" })).toBeNull();
  });
});
