import type { JsonValue } from "./types";

export type TerminalSessionType =
  | "agent"
  | "worker"
  | "manual"
  | "debug"
  | "process"
  | "log_stream";
export type TerminalSessionMode = "read_only" | "interactive";
export type TerminalSessionStatus =
  | "starting"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "lost";
export type TerminalStreamChannel = "stdout" | "stderr" | "log" | "system";

export interface TerminalCapabilities {
  interactive_input: boolean;
  resize: boolean;
  reconnect: boolean;
  terminate: boolean;
  pty: boolean;
}

export interface TerminalDimensions {
  columns: number;
  rows: number;
}

export interface TerminalSessionContext {
  project_id: string;
  workspace_id: string;
  task_id: string | null;
  run_id: string | null;
  worker_id: string | null;
  node_id: string | null;
}

export interface CanonicalTerminalSession {
  id: string;
  session_type: TerminalSessionType;
  context: TerminalSessionContext;
  mode: TerminalSessionMode;
  owner_actor_ref: string;
  adapter_id: string;
  capabilities: TerminalCapabilities;
  status: TerminalSessionStatus;
  started_at: string;
  ended_at: string | null;
  encoding: string;
  dimensions: TerminalDimensions | null;
  policy_classification: string[];
  adapter_metadata: Array<{ namespace: string; values: Record<string, JsonValue> }>;
}

export interface TerminalFrame {
  id: string;
  session_id: string;
  sequence: number;
  channel: TerminalStreamChannel;
  data: string;
  occurred_at: string;
  final: boolean;
}

export interface TerminalAttachment {
  id: string;
  session_id: string;
  actor_ref: string;
  status: "connected" | "detached";
  connected_at: string;
  detached_at: string | null;
  after_sequence: number;
}

export interface CreateTerminalSessionInput {
  workspace_id: string;
  session_type: TerminalSessionType;
  mode: TerminalSessionMode;
  task_id?: string;
  run_id?: string;
  worker_id?: string;
  node_id?: string;
  adapter_id?: string;
  encoding?: string;
  dimensions?: TerminalDimensions;
  policy_classification?: string[];
  approval_id?: string;
}

export type TerminalStreamMessage =
  | {
      type: "session.snapshot";
      request_id: string;
      correlation_id: string;
      session: CanonicalTerminalSession;
      attachment: TerminalAttachment;
    }
  | { type: "stream.frame"; frame: TerminalFrame }
  | { type: "session.status"; session: CanonicalTerminalSession }
  | { type: "error"; code: string; message?: string; details?: Record<string, JsonValue> }
  | { type: "pong" };

export function buildTerminalStreamUrl(
  baseUrl: string,
  sessionId: string,
  afterSequence = 0,
  pageOrigin = typeof window === "undefined" ? "http://localhost" : window.location.origin,
): string {
  if (afterSequence < 0 || !Number.isInteger(afterSequence)) {
    throw new Error("afterSequence must be a non-negative integer");
  }
  const base = baseUrl.trim() || pageOrigin;
  const normalizedBase = base.endsWith("/") ? base.slice(0, -1) : base;
  const url = new URL(
    `${normalizedBase}/api/v1/terminal-sessions/${encodeURIComponent(sessionId)}/stream`,
    pageOrigin,
  );
  if (url.protocol === "https:") url.protocol = "wss:";
  else if (url.protocol === "http:") url.protocol = "ws:";
  else if (url.protocol !== "ws:" && url.protocol !== "wss:") {
    throw new Error(`unsupported Control Plane protocol: ${url.protocol}`);
  }
  if (afterSequence > 0) url.searchParams.set("after_sequence", String(afterSequence));
  return url.toString();
}

export function parseTerminalStreamMessage(value: unknown): TerminalStreamMessage | null {
  if (!isRecord(value) || typeof value.type !== "string") return null;
  switch (value.type) {
    case "session.snapshot":
      return isTerminalSession(value.session) && isTerminalAttachment(value.attachment)
        ? (value as unknown as TerminalStreamMessage)
        : null;
    case "stream.frame":
      return isTerminalFrame(value.frame) ? (value as unknown as TerminalStreamMessage) : null;
    case "session.status":
      return isTerminalSession(value.session) ? (value as unknown as TerminalStreamMessage) : null;
    case "error":
      return typeof value.code === "string" ? (value as unknown as TerminalStreamMessage) : null;
    case "pong":
      return { type: "pong" };
    default:
      return null;
  }
}

function isTerminalSession(value: unknown): value is CanonicalTerminalSession {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.session_type === "string" &&
    typeof value.mode === "string" &&
    typeof value.status === "string" &&
    isRecord(value.context) &&
    isRecord(value.capabilities)
  );
}

function isTerminalFrame(value: unknown): value is TerminalFrame {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.session_id === "string" &&
    typeof value.sequence === "number" &&
    typeof value.channel === "string" &&
    typeof value.data === "string" &&
    typeof value.final === "boolean"
  );
}

function isTerminalAttachment(value: unknown): value is TerminalAttachment {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.session_id === "string" &&
    typeof value.status === "string"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
