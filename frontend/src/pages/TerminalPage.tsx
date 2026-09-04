import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { ControlPlaneClient, isControlPlaneError } from "../api/client";
import {
  parseTerminalStreamMessage,
  type CanonicalTerminalSession,
  type TerminalFrame,
  type TerminalSessionMode,
  type TerminalSessionType,
} from "../api/terminal";
import type { Page } from "../api/types";
import { AppLink } from "../app/router";
import {
  CanonicalId,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

type ConnectionState = "idle" | "connecting" | "open" | "closed" | "error";
type PendingApproval = { approvalId: string; sessionId?: string };
type PendingInputApproval = { approvalId: string; data: string };

const TERMINAL_TYPES: Array<{ value: TerminalSessionType; label: string }> = [
  { value: "manual", label: "Manual" },
  { value: "debug", label: "Debug" },
  { value: "process", label: "Process" },
  { value: "log_stream", label: "Log stream" },
];

export function terminalSessionFilters(
  projectId: string,
  workspaceId: string,
  status: string,
): Record<string, string> {
  const filters: Record<string, string> = {};
  const project = projectId.trim();
  const workspace = workspaceId.trim();
  if (project) filters.project_id = project;
  if (workspace) filters.workspace_id = workspace;
  if (status) filters.status = status;
  return filters;
}

export function TerminalPage({ client }: { client: ControlPlaneClient }) {
  const [page, setPage] = useState<Page<CanonicalTerminalSession> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [projectFilter, setProjectFilter] = useState("");
  const [workspaceFilter, setWorkspaceFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [frames, setFrames] = useState<TerminalFrame[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [reconnectToken, setReconnectToken] = useState(0);
  const [input, setInput] = useState("");
  const [pendingInputApproval, setPendingInputApproval] =
    useState<PendingInputApproval | null>(null);
  const [terminationApprovalId, setTerminationApprovalId] = useState<string | null>(null);
  const [mutating, setMutating] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const lastSequenceRef = useRef(0);
  const lastSubmittedInputRef = useRef("");

  const selected = useMemo(
    () => page?.items.find((session) => session.id === selectedId) ?? null,
    [page, selectedId],
  );

  const replaceSession = useCallback((session: CanonicalTerminalSession) => {
    setPage((current) => {
      if (!current) return current;
      const exists = current.items.some((item) => item.id === session.id);
      return {
        ...current,
        items: exists
          ? current.items.map((item) => (item.id === session.id ? session : item))
          : [session, ...current.items],
        total: exists ? current.total : current.total + 1,
      };
    });
  }, []);

  const load = useCallback(async () => {
    const projectId = projectFilter.trim();
    if (!projectId) {
      setPage({ items: [], next_cursor: null, total: 0, limit: 100 });
      setSelectedId(null);
      setError(null);
      return;
    }

    try {
      const nextPage = await client.listTerminalSessions({
        limit: 100,
        sort: "started_at",
        direction: "desc",
        filters: terminalSessionFilters(projectId, workspaceFilter, statusFilter),
      });
      setPage(nextPage);
      setSelectedId((current) =>
        current && nextPage.items.some((item) => item.id === current)
          ? current
          : (nextPage.items[0]?.id ?? null),
      );
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, projectFilter, statusFilter, workspaceFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setFrames([]);
    setStreamError(null);
    setConnection(selectedId ? "connecting" : "idle");
    setPendingInputApproval(null);
    setTerminationApprovalId(null);
    lastSubmittedInputRef.current = "";
    lastSequenceRef.current = 0;
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    let disposed = false;
    setConnection("connecting");
    setStreamError(null);
    const socket = new WebSocket(
      client.terminalStreamUrl(selectedId, lastSequenceRef.current),
      "platform.terminal.v1",
    );
    socketRef.current = socket;

    socket.onopen = () => {
      if (!disposed) setConnection("open");
    };
    socket.onmessage = (event) => {
      if (disposed || typeof event.data !== "string") return;
      try {
        const message = parseTerminalStreamMessage(JSON.parse(event.data) as unknown);
        if (!message) {
          setStreamError("Control Plane sent an invalid terminal stream message.");
          return;
        }
        if (message.type === "session.snapshot" || message.type === "session.status") {
          replaceSession(message.session);
          return;
        }
        if (message.type === "stream.frame") {
          if (message.frame.sequence <= lastSequenceRef.current) return;
          lastSequenceRef.current = message.frame.sequence;
          setFrames((current) => [...current, message.frame]);
          return;
        }
        if (message.type === "error") {
          const approvalId = stringValue(message.details?.approval_id);
          const outcome = stringValue(message.details?.authorization_outcome);
          if (
            outcome === "require_approval" &&
            approvalId &&
            lastSubmittedInputRef.current
          ) {
            setPendingInputApproval({
              approvalId,
              data: lastSubmittedInputRef.current,
            });
          }
          setStreamError(message.message ?? message.code);
        }
      } catch {
        setStreamError("Control Plane sent malformed terminal stream JSON.");
      }
    };
    socket.onerror = () => {
      if (!disposed) setConnection("error");
    };
    socket.onclose = (event) => {
      if (!disposed) {
        setConnection(event.code === 1000 ? "closed" : "error");
        if (event.reason) setStreamError(event.reason);
      }
      if (socketRef.current === socket) socketRef.current = null;
    };

    return () => {
      disposed = true;
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "detach" }));
      }
      socket.close(1000, "view changed");
      if (socketRef.current === socket) socketRef.current = null;
    };
  }, [client, reconnectToken, replaceSession, selectedId]);

  const sendInput = (event: FormEvent) => {
    event.preventDefault();
    const socket = socketRef.current;
    if (!selected || !input || !socket || socket.readyState !== WebSocket.OPEN) return;
    lastSubmittedInputRef.current = input;
    setPendingInputApproval(null);
    socket.send(JSON.stringify({ type: "input", data: input }));
    setInput("");
  };

  const resumeApprovedInput = () => {
    const socket = socketRef.current;
    if (!pendingInputApproval || !socket || socket.readyState !== WebSocket.OPEN) return;
    lastSubmittedInputRef.current = pendingInputApproval.data;
    socket.send(
      JSON.stringify({
        type: "input",
        data: pendingInputApproval.data,
        approval_id: pendingInputApproval.approvalId,
      }),
    );
    setPendingInputApproval(null);
  };

  const terminate = async () => {
    if (!selected || !selected.capabilities.terminate) return;
    if (
      !terminationApprovalId &&
      !window.confirm(
        `Terminate terminal session ${selected.id}? This is a destructive execution action.`,
      )
    ) {
      return;
    }
    setMutating(true);
    try {
      replaceSession(
        await client.terminateTerminalSession(
          selected.id,
          "terminated from terminal UI",
          terminationApprovalId ?? undefined,
        ),
      );
      setTerminationApprovalId(null);
      setStreamError(null);
      setError(null);
    } catch (nextError) {
      const approval = approvalFromError(nextError);
      if (approval) setTerminationApprovalId(approval.approvalId);
      setError(nextError);
    } finally {
      setMutating(false);
    }
  };

  return (
    <div className="stack terminal-page">
      <header className="page-header">
        <p className="eyebrow">Execution sessions</p>
        <h1>Terminal</h1>
        <p>
          Inspect and, only when policy and adapter capabilities allow it, interact with canonical
          execution sessions through the Control Plane gateway.
        </p>
      </header>

      <CreateReferenceSession
        client={client}
        onCreated={(session) => {
          replaceSession(session);
          setProjectFilter(session.context.project_id);
          setSelectedId(session.id);
        }}
      />

      <div className="toolbar card terminal-filters">
        <label>
          Project ID
          <input
            value={projectFilter}
            onChange={(event) => setProjectFilter(event.target.value)}
            placeholder="project_..."
          />
        </label>
        <label>
          Workspace ID
          <input
            value={workspaceFilter}
            onChange={(event) => setWorkspaceFilter(event.target.value)}
            placeholder="workspace_..."
          />
        </label>
        <label>
          Status
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All</option>
            <option value="starting">Starting</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
            <option value="lost">Lost</option>
          </select>
        </label>
        <button disabled={!projectFilter.trim()} onClick={() => void load()}>
          Refresh
        </button>
      </div>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {!page ? (
        <LoadingState />
      ) : !projectFilter.trim() ? (
        <EmptyState title="Choose a project" />
      ) : page.items.length === 0 ? (
        <EmptyState title="No terminal sessions" />
      ) : (
        <div className="terminal-layout">
          <section className="card terminal-session-list" aria-label="Terminal sessions">
            <h2>Sessions</h2>
            <div className="terminal-session-scroll">
              {page.items.map((session) => (
                <button
                  type="button"
                  className={
                    session.id === selectedId
                      ? "terminal-session-row selected"
                      : "terminal-session-row"
                  }
                  key={session.id}
                  onClick={() => setSelectedId(session.id)}
                >
                  <span>
                    <strong>{session.session_type.replace("_", " ")}</strong>
                    <StatusBadge value={session.status} />
                  </span>
                  <CanonicalId value={session.id} />
                  <small>{session.context.workspace_id}</small>
                </button>
              ))}
            </div>
          </section>

          <section className="stack terminal-workarea">
            {selected ? (
              <>
                <TerminalContext session={selected} connection={connection} />
                <div className="card terminal-card">
                  <div className="terminal-toolbar">
                    <div>
                      <span
                        className={`live live-${connection === "error" ? "reconnecting" : connection}`}
                      >
                        {connection}
                      </span>
                      <span className="terminal-mode">{selected.mode.replace("_", " ")}</span>
                    </div>
                    <div className="actions">
                      {selected.capabilities.reconnect && connection !== "open" ? (
                        <button onClick={() => setReconnectToken((value) => value + 1)}>
                          Reconnect
                        </button>
                      ) : null}
                      {selected.capabilities.terminate && !isTerminalStatus(selected.status) ? (
                        <button
                          className="danger-action"
                          disabled={mutating}
                          onClick={() => void terminate()}
                        >
                          {terminationApprovalId
                            ? "Resume approved termination"
                            : "Terminate session"}
                        </button>
                      ) : null}
                    </div>
                  </div>
                  <div className="terminal-warning" role="note">
                    Terminal access is policy-scoped. Privileged input and termination remain auditable
                    Control Plane actions; this view is not host SSH.
                  </div>
                  <TerminalViewport frames={frames} />
                  {streamError ? <div className="state state-warning">{streamError}</div> : null}
                  {pendingInputApproval ? (
                    <div className="state state-warning" role="status">
                      <strong>Input approval required</strong>
                      <p>
                        Approval <CanonicalId value={pendingInputApproval.approvalId} /> is bound to the
                        exact submitted input. After the canonical approval is granted, resume that same
                        request here.
                      </p>
                      <button
                        disabled={connection !== "open"}
                        onClick={resumeApprovedInput}
                      >
                        Resume approved input
                      </button>
                    </div>
                  ) : null}
                  {selected.mode === "interactive" && selected.capabilities.interactive_input ? (
                    <form className="terminal-input" onSubmit={sendInput}>
                      <textarea
                        aria-label="Terminal input"
                        rows={3}
                        value={input}
                        onChange={(event) => setInput(event.target.value)}
                        placeholder="Input is sent only through the canonical session gateway"
                        disabled={connection !== "open"}
                      />
                      <button className="primary" disabled={connection !== "open" || !input}>
                        Send input
                      </button>
                    </form>
                  ) : (
                    <p className="terminal-readonly">
                      Read-only session — input is disabled by canonical capability metadata.
                    </p>
                  )}
                </div>
              </>
            ) : null}
          </section>
        </div>
      )}
    </div>
  );
}

function CreateReferenceSession({
  client,
  onCreated,
}: {
  client: ControlPlaneClient;
  onCreated: (session: CanonicalTerminalSession) => void;
}) {
  const [projectId, setProjectId] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [sessionType, setSessionType] = useState<TerminalSessionType>("manual");
  const [mode, setMode] = useState<TerminalSessionMode>("read_only");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);

  const clearPendingApproval = () => {
    setPendingApproval(null);
    setError(null);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!projectId.trim() || !workspaceId.trim()) return;
    setCreating(true);
    try {
      const session = await client.createTerminalSession(projectId.trim(), {
        workspace_id: workspaceId.trim(),
        session_type: sessionType,
        mode: sessionType === "log_stream" ? "read_only" : mode,
        session_id: pendingApproval?.sessionId,
        approval_id: pendingApproval?.approvalId,
      });
      setError(null);
      setPendingApproval(null);
      onCreated(session);
    } catch (nextError) {
      const approval = approvalFromError(nextError);
      if (approval?.sessionId) setPendingApproval(approval);
      setError(nextError);
    } finally {
      setCreating(false);
    }
  };

  return (
    <section className="card">
      <h2>Open reference session</h2>
      <p className="terminal-readonly">
        The bundled reference adapter is deliberately not a host shell. Manual interactive creation may
        require explicit approval under the active authorization policy.
      </p>
      <form className="form-grid terminal-create" onSubmit={(event) => void submit(event)}>
        <label>
          Project ID
          <input
            required
            value={projectId}
            onChange={(event) => {
              setProjectId(event.target.value);
              clearPendingApproval();
            }}
            placeholder="project_..."
          />
        </label>
        <label>
          Workspace ID
          <input
            required
            value={workspaceId}
            onChange={(event) => {
              setWorkspaceId(event.target.value);
              clearPendingApproval();
            }}
            placeholder="workspace_..."
          />
        </label>
        <label>
          Session type
          <select
            value={sessionType}
            onChange={(event) => {
              const next = event.target.value as TerminalSessionType;
              setSessionType(next);
              if (next === "log_stream") setMode("read_only");
              clearPendingApproval();
            }}
          >
            {TERMINAL_TYPES.map((item) => (
              <option value={item.value} key={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Mode
          <select
            value={sessionType === "log_stream" ? "read_only" : mode}
            disabled={sessionType === "log_stream"}
            onChange={(event) => {
              setMode(event.target.value as TerminalSessionMode);
              clearPendingApproval();
            }}
          >
            <option value="read_only">Read only</option>
            <option value="interactive">Interactive</option>
          </select>
        </label>
        <button
          className="primary"
          disabled={creating || !projectId.trim() || !workspaceId.trim()}
        >
          {creating
            ? "Opening…"
            : pendingApproval
              ? "Resume approved request"
              : "Open session"}
        </button>
      </form>
      {pendingApproval ? (
        <div className="state state-warning" role="status">
          <strong>Approval-bound session reserved</strong>
          <p>
            Session <CanonicalId value={pendingApproval.sessionId ?? "unknown"} /> is reserved for
            approval <CanonicalId value={pendingApproval.approvalId} />. After that canonical approval
            is granted, resume the exact request instead of creating a new session.
          </p>
        </div>
      ) : null}
      {error ? <ErrorState error={error} /> : null}
    </section>
  );
}

function TerminalContext({
  session,
  connection,
}: {
  session: CanonicalTerminalSession;
  connection: ConnectionState;
}) {
  const refs = session.context;
  return (
    <section className="card">
      <div className="detail-header">
        <div>
          <h2>Session context</h2>
          <CanonicalId value={session.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={session.status} />
          <span className={`live live-${connection}`}>{connection}</span>
        </div>
      </div>
      <dl className="terminal-context">
        <div>
          <dt>Project</dt>
          <dd>
            <AppLink href={`/projects/${refs.project_id}`}>
              <CanonicalId value={refs.project_id} />
            </AppLink>
          </dd>
        </div>
        <div>
          <dt>Workspace</dt>
          <dd>
            <AppLink href={`/workspaces/${refs.workspace_id}`}>
              <CanonicalId value={refs.workspace_id} />
            </AppLink>
          </dd>
        </div>
        {refs.task_id ? (
          <div>
            <dt>Task</dt>
            <dd>
              <AppLink href={`/tasks/${refs.task_id}`}>
                <CanonicalId value={refs.task_id} />
              </AppLink>
            </dd>
          </div>
        ) : null}
        {refs.run_id ? (
          <div>
            <dt>Run</dt>
            <dd>
              <AppLink href={`/runs/${refs.run_id}`}>
                <CanonicalId value={refs.run_id} />
              </AppLink>
            </dd>
          </div>
        ) : null}
        <div>
          <dt>Artifacts</dt>
          <dd>
            <AppLink href="/files">Open canonical artifact view</AppLink>
          </dd>
        </div>
        <div>
          <dt>Timeline</dt>
          <dd>
            <AppLink href="/events">Open canonical timeline view</AppLink>
          </dd>
        </div>
        {refs.worker_id ? (
          <div>
            <dt>Worker</dt>
            <dd>
              <CanonicalId value={refs.worker_id} />
            </dd>
          </div>
        ) : null}
        {refs.node_id ? (
          <div>
            <dt>Node</dt>
            <dd>
              <CanonicalId value={refs.node_id} />
            </dd>
          </div>
        ) : null}
        <div>
          <dt>Adapter</dt>
          <dd>{session.adapter_id}</dd>
        </div>
        <div>
          <dt>Owner</dt>
          <dd>{session.owner_actor_ref}</dd>
        </div>
        <div>
          <dt>Encoding</dt>
          <dd>{session.encoding}</dd>
        </div>
      </dl>
    </section>
  );
}

function TerminalViewport({ frames }: { frames: TerminalFrame[] }) {
  if (frames.length === 0) {
    return <div className="terminal-viewport terminal-empty">No streamed output yet.</div>;
  }
  return (
    <pre className="terminal-viewport" aria-label="Terminal output">
      {frames.map((frame) => (
        <span className={`terminal-${frame.channel}`} key={frame.id}>
          {frame.data}
        </span>
      ))}
    </pre>
  );
}

function approvalFromError(error: unknown): PendingApproval | null {
  if (!isControlPlaneError(error)) return null;
  const details = error.body.details ?? {};
  if (stringValue(details.authorization_outcome) !== "require_approval") return null;
  const approvalId = stringValue(details.approval_id);
  if (!approvalId) return null;
  return {
    approvalId,
    sessionId: stringValue(details.session_id),
  };
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function isTerminalStatus(status: CanonicalTerminalSession["status"]): boolean {
  return ["completed", "failed", "cancelled", "lost"].includes(status);
}
