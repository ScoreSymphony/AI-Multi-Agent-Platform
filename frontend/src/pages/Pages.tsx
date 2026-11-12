import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ControlPlaneClient, prettyJson } from "../api/client";
import { TaskEventStream, type LiveConnectionState } from "../api/live";
import type {
  APImanifest,
  CanonicalRun,
  CanonicalTask,
  HealthStatus,
  OwnerType,
  Page,
  TimelineItem,
} from "../api/types";
import { AppLink, useRouter } from "../app/router";
import { useFrontendCustomization } from "../customization/FrontendCustomizationProvider";
import { resolveDashboardWidgets } from "../customization/model";
import {
  CanonicalId,
  Card,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";
import { isCanonicalId } from "../platform/id";
import { usePermissionHint } from "../security/permissions";

export function OverviewPage({ client }: { client: ControlPlaneClient }) {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [tasks, setTasks] = useState<Page<CanonicalTask> | null>(null);
  const [runs, setRuns] = useState<Page<CanonicalRun> | null>(null);
  const [failures, setFailures] = useState<string[]>([]);
  const { draft } = useFrontendCustomization();
  const dashboardWidgets = resolveDashboardWidgets(draft.dashboard);

  useEffect(() => {
    let active = true;
    void Promise.allSettled([
      client.health(),
      client.listTasks({ limit: 8, sort: "updated_at", direction: "desc" }),
      client.listRuns({ limit: 8, sort: "updated_at", direction: "desc" }),
    ]).then(([healthResult, taskResult, runResult]) => {
      if (!active) return;
      const nextFailures: string[] = [];
      if (healthResult.status === "fulfilled") setHealth(healthResult.value);
      else nextFailures.push("health");
      if (taskResult.status === "fulfilled") setTasks(taskResult.value);
      else nextFailures.push("tasks");
      if (runResult.status === "fulfilled") setRuns(runResult.value);
      else nextFailures.push("runs");
      setFailures(nextFailures);
    });
    return () => {
      active = false;
    };
  }, [client]);

  if (!health && !tasks && !runs && failures.length === 0) return <LoadingState />;
  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Control Plane</p>
        <h1>Platform overview</h1>
        <p>Canonical status and recent Task/Run activity from `/api/v1`.</p>
      </header>
      {failures.length > 0 && (
        <DegradedState
          title="Partial platform data"
          detail={`Unavailable sections: ${failures.join(", ")}. Other Control Plane data remains usable.`}
        />
      )}
      <div className="dashboard-widgets">
        {dashboardWidgets.map((widget) => {
          if (widget === "metrics") {
            return (
              <div className="metrics dashboard-widget-wide" key={widget}>
                <Metric label="Readiness" value={health ? (health.ready ? "ready" : "degraded") : "unknown"} />
                <Metric label="Tasks" value={tasks?.total ?? "—"} />
                <Metric label="Runs" value={runs?.total ?? "—"} />
                <Metric
                  label="Active runs"
                  value={runs?.items.filter((run) => ["queued", "starting", "running"].includes(run.status)).length ?? "—"}
                />
              </div>
            );
          }
          if (widget === "recent-tasks") {
            return <Card key={widget} title="Recent tasks"><TaskTable tasks={tasks?.items ?? []} compact /></Card>;
          }
          return <Card key={widget} title="Recent runs"><RunTable runs={runs?.items ?? []} compact /></Card>;
        })}
      </div>
    </div>
  );
}

export function TasksPage({ client }: { client: ControlPlaneClient }) {
  const [page, setPage] = useState<Page<CanonicalTask> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [filter, setFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const { navigate } = useRouter();

  const load = useCallback(async () => {
    setError(null);
    try {
      setPage(await client.listTasks({ limit: 100, sort: "updated_at", direction: "desc", filters: filter ? { status: filter } : undefined }));
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, filter]);
  useEffect(() => { void load(); }, [load]);

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreating(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      const task = await client.createTask({
        title: String(form.get("title") ?? ""),
        objective: String(form.get("objective") ?? ""),
        owner_type: String(form.get("owner_type") ?? "user") as OwnerType,
        owner_id: String(form.get("owner_id") ?? ""),
        project_id: String(form.get("project_id") ?? "").trim() || undefined,
      });
      navigate(`/tasks/${task.id}`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="stack">
      <header className="page-header"><p className="eyebrow">Canonical work</p><h1>Tasks</h1><p>Create and inspect Tasks through the platform Control Plane.</p></header>
      <Card title="Create task">
        <form className="form-grid" onSubmit={create}>
          <label>Title<input name="title" required /></label>
          <label>Objective<input name="objective" required /></label>
          <label>Owner type<select name="owner_type" defaultValue="user"><option value="user">user</option><option value="organization">organization</option><option value="team">team</option><option value="service">service</option></select></label>
          <label>Owner ID<input name="owner_id" defaultValue="local" required /></label>
          <label>Project ID (optional)<input name="project_id" placeholder="project_…" /></label>
          <button className="primary" disabled={creating}>{creating ? "Creating…" : "Create task"}</button>
        </form>
      </Card>
      <div className="toolbar"><label>Status<select value={filter} onChange={(event) => setFilter(event.target.value)}><option value="">all</option>{["draft","ready","running","waiting","succeeded","failed","cancelled"].map((value) => <option key={value}>{value}</option>)}</select></label><button onClick={() => void load()}>Refresh</button></div>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : !page ? <LoadingState /> : <TaskTable tasks={page.items} />}
    </div>
  );
}

export function TaskDetailPage({ client, taskId }: { client: ControlPlaneClient; taskId: string }) {
  const [task, setTask] = useState<CanonicalTask | null>(null);
  const [runs, setRuns] = useState<CanonicalRun[]>([]);
  const [events, setEvents] = useState<TimelineItem[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [liveState, setLiveState] = useState<LiveConnectionState>("connecting");
  const permission = usePermissionHint("task:command", taskId);

  const load = useCallback(async () => {
    if (!isCanonicalId(taskId)) {
      setError(new Error("This route does not contain a valid canonical Task ID."));
      return;
    }
    try {
      const [nextTask, nextRuns, timeline] = await Promise.all([
        client.getTask(taskId),
        client.listTaskRuns(taskId, { limit: 100, sort: "created_at", direction: "desc" }),
        client.timeline(taskId, { limit: 100, direction: "asc" }),
      ]);
      setTask(nextTask);
      setRuns(nextRuns.items);
      setEvents(timeline.items);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, taskId]);

  useEffect(() => {
    void load();
    const stream = new TaskEventStream({ baseUrl: client.baseUrl, taskId, onEvent: () => void load(), onError: () => setLiveState("reconnecting"), onState: setLiveState });
    stream.open();
    return () => stream.close();
  }, [client, load, taskId]);

  const command = async (action: "queue" | "start" | "cancel" | "retry") => {
    setBusy(true);
    try {
      if (action === "queue") await client.queueTask(taskId);
      if (action === "start") await client.startTask(taskId);
      if (action === "cancel") await client.cancelTask(taskId);
      if (action === "retry") await client.retryTask(taskId);
      await load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error && !task) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!task) return <LoadingState />;
  const canQueue = task.status === "draft";
  const canStart = task.status === "ready";
  const canCancel = ["draft", "ready", "running", "waiting"].includes(task.status);
  const canRetry = task.status === "failed";

  return (
    <div className="stack">
      <header className="page-header detail-header"><div><p className="eyebrow">Task</p><h1>{task.title}</h1><CanonicalId value={task.id} /></div><div className="detail-status"><StatusBadge value={task.status} /><span className={`live live-${liveState}`}>{liveState}</span></div></header>
      {error != null && <ErrorState error={error} onRetry={() => void load()} />}
      {permission === "denied" && <DegradedState title="Permission hint" detail="The current client hint marks Task commands as denied. The server remains authoritative." />}
      <div className="actions" aria-label="Task lifecycle commands">{canQueue && <button disabled={busy} onClick={() => void command("queue")}>Queue</button>}{canStart && <button className="primary" disabled={busy} onClick={() => void command("start")}>Start</button>}{canCancel && <button disabled={busy} onClick={() => void command("cancel")}>Cancel</button>}{canRetry && <button className="primary" disabled={busy} onClick={() => void command("retry")}>Retry</button>}<button disabled={busy} onClick={() => void load()}>Refresh</button></div>
      <div className="grid-two">
        <Card title="Task details"><DefinitionList values={{ objective: task.objective, revision: task.revision, project: task.project_id ?? "—", owner: `${task.owner.type}:${task.owner.id}`, correlation: task.correlation_id ?? "—", updated: formatDate(task.updated_at) }} /></Card>
        <Card title="Canonical references"><ReferenceList label="Plan" values={task.plan_ref ? [task.plan_ref] : []} /><ReferenceList label="Steps" values={task.step_ids} /><ReferenceList label="Artifacts" values={task.artifact_ids} /><ReferenceList label="Results" values={task.result_ids} /></Card>
      </div>
      <Card title="Runs"><RunTable runs={runs} /></Card>
      <Card title="Timeline">{events.length === 0 ? <EmptyState title="No events yet" /> : <ol className="timeline">{events.map((event) => <li key={event.id}><div><strong>{timelineLabel(event)}</strong><small>{formatDate(timelineTimestamp(event))}</small></div><CanonicalId value={event.id} /></li>)}</ol>}</Card>
    </div>
  );
}

export function RunsPage({ client }: { client: ControlPlaneClient }) {
  const [page, setPage] = useState<Page<CanonicalRun> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback(async () => {
    try {
      setPage(await client.listRuns({ limit: 100, sort: "updated_at", direction: "desc" }));
      setError(null);
    } catch (nextError) { setError(nextError); }
  }, [client]);
  useEffect(() => { void load(); }, [load]);
  return <div className="stack"><header className="page-header"><p className="eyebrow">Execution</p><h1>Runs</h1><p>Canonical attempts across Tasks.</p></header>{error ? <ErrorState error={error} onRetry={() => void load()} /> : !page ? <LoadingState /> : <RunTable runs={page.items} />}</div>;
}

export function RunDetailPage({ client, runId }: { client: ControlPlaneClient; runId: string }) {
  const [run, setRun] = useState<CanonicalRun | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!isCanonicalId(runId)) {
      setError(new Error("This route does not contain a valid canonical Run ID."));
      return;
    }
    try {
      setRun(await client.getRun(runId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !run) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!run) return <LoadingState />;

  const canCancel = ["queued", "starting", "running"].includes(run.status);
  const cancel = async () => {
    if (!canCancel || !confirmRunCancellation(run.id)) return;
    setBusy(true);
    try {
      setRun(await client.cancelRun(run.task_id, run.id));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div><p className="eyebrow">Run</p><h1>Attempt {run.attempt}</h1><CanonicalId value={run.id} /></div>
        <StatusBadge value={run.status} />
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <div className="actions">
        <AppLink href="/runs">Back to Runs</AppLink>
        <AppLink href={`/tasks/${run.task_id}`}>Open Task</AppLink>
        {canCancel ? <button type="button" disabled={busy} onClick={() => void cancel()}>{busy ? "Cancelling…" : "Cancel Run"}</button> : null}
        <button type="button" disabled={busy} onClick={() => void load()}>Refresh</button>
      </div>
      <RunFailureDiagnostics run={run} />
      {run.recovery_required ? <DegradedState title="Recovery required" detail={run.recovery_reason ?? "The canonical Run is marked for recovery."} /> : null}
      <div className="grid-two">
        <Card title="Run details">
          <DefinitionList values={{ task: run.task_id, subject: `${run.subject_type}:${run.subject_id}`, trace: run.trace_id ?? "—", correlation: run.correlation_id, started: run.started_at ? formatDate(run.started_at) : "—", finished: run.finished_at ? formatDate(run.finished_at) : "—" }} />
        </Card>
        <Card title="References">
          <ReferenceList label="Artifacts" values={run.artifact_ids} />
          <ReferenceList label="Results" values={run.result_ids} />
        </Card>
      </div>
      <Card title="Workspace provenance">
        <RunWorkspaceBinding run={run} />
      </Card>
      <Card title="Output"><pre>{prettyJson(run.output)}</pre></Card>
    </div>
  );
}

export function RunFailureDiagnostics({
  run,
}: {
  run: Pick<CanonicalRun, "status" | "task_id" | "error">;
}) {
  if (!run.error) return null;
  return (
    <div className="stack">
      <DegradedState title={`${run.error.category}: ${run.error.code}`} detail={run.error.message} />
      {run.status === "failed" ? (
        <p>
          Supported next action: <AppLink href={`/tasks/${run.task_id}`}>Open Task for retry</AppLink>.
        </p>
      ) : null}
    </div>
  );
}

export function RunWorkspaceBinding({
  run,
}: {
  run: Pick<CanonicalRun, "workspace_id" | "workspace_snapshot_id" | "workspace_content_checksum">;
}) {
  if (!run.workspace_id) {
    return <p>No canonical Workspace binding is recorded for this Run.</p>;
  }
  return (
    <div className="stack">
      <p><AppLink href={`/workspaces/${run.workspace_id}`}>Open Workspace</AppLink></p>
      <DefinitionList values={{
        Workspace: run.workspace_id,
        "Workspace snapshot": run.workspace_snapshot_id ?? "not recorded",
        "Content checksum": run.workspace_content_checksum ?? "not recorded",
      }} />
    </div>
  );
}

export function confirmRunCancellation(runId: string): boolean {
  return window.confirm(
    `Cancel Run "${runId}"? The canonical Control Plane will determine the resulting Run and Task lifecycle state.`,
  );
}

export function UnavailablePage({ item, manifest }: { item: { label: string; apiResource?: string }; manifest: APImanifest | null }) {
  const registered = item.apiResource ? manifest?.resources.includes(item.apiResource) : false;
  return <div className="stack"><header className="page-header"><p className="eyebrow">Stable navigation shell</p><h1>{item.label}</h1></header><DegradedState title={registered ? "UI integration pending" : "Canonical subsystem unavailable"} detail={registered ? `The Control Plane advertises ${item.apiResource}, but this client does not provide a dedicated product view for it.` : "This route is intentionally stable, but its owning canonical subsystem/API is not currently available. No private backend fallback is used."} /></div>;
}

function TaskTable({ tasks, compact = false }: { tasks: CanonicalTask[]; compact?: boolean }) {
  if (tasks.length === 0) return <EmptyState title="No tasks" />;
  return <div className="table-wrap"><table><thead><tr><th>Task</th><th>Status</th>{!compact && <th>Updated</th>}</tr></thead><tbody>{tasks.map((task) => <tr key={task.id}><td><AppLink href={`/tasks/${task.id}`}>{task.title}</AppLink><div><CanonicalId value={task.id} /></div></td><td><StatusBadge value={task.status} /></td>{!compact && <td>{formatDate(task.updated_at)}</td>}</tr>)}</tbody></table></div>;
}

function RunTable({ runs, compact = false }: { runs: CanonicalRun[]; compact?: boolean }) {
  if (runs.length === 0) return <EmptyState title="No runs" />;
  return <div className="table-wrap"><table><thead><tr><th>Run</th><th>Status</th>{!compact && <th>Task</th>}<th>Attempt</th></tr></thead><tbody>{runs.map((run) => <tr key={run.id}><td><AppLink href={`/runs/${run.id}`}><CanonicalId value={run.id} /></AppLink></td><td><StatusBadge value={run.status} /></td>{!compact && <td><AppLink href={`/tasks/${run.task_id}`}><CanonicalId value={run.task_id} /></AppLink></td>}<td>{run.attempt}</td></tr>)}</tbody></table></div>;
}

function ReferenceList({ label, values }: { label: string; values: string[] }) {
  const collection =
    label === "Artifacts" ? "artifacts" :
    label === "Results" ? "results" :
    label === "Plan" ? "plans" :
    label === "Steps" ? "steps" :
    null;
  return (
    <div className="reference-group">
      <strong>{label}</strong>
      {values.length ? (
        <ul>
          {values.map((value) => (
            <li key={value}>
              {collection ? <AppLink href={`/${collection}/${value}`}><CanonicalId value={value} /></AppLink> : <CanonicalId value={value} />}
            </li>
          ))}
        </ul>
      ) : <span>—</span>}
    </div>
  );
}
function DefinitionList({ values }: { values: Record<string, string | number> }) { return <dl>{Object.entries(values).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>; }
function Metric({ label, value }: { label: string; value: string | number }) { return <div className="metric"><span>{label}</span><strong>{value}</strong></div>; }
function formatDate(value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString(); }
function timelineLabel(item: TimelineItem): string { return item.type === "event" ? item.event_type : `${item.component}: ${item.event_name}`; }
function timelineTimestamp(item: TimelineItem): string { return item.type === "event" ? item.occurred_at : item.timestamp; }
