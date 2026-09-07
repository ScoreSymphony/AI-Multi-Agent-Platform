import { useCallback, useEffect, useRef, useState } from "react";
import { ControlPlaneClient } from "../api/client";
import {
  describeLiveStreamError,
  TaskEventStream,
  type LiveConnectionState,
} from "../api/live";
import type {
  CanonicalProject,
  CanonicalRun,
  CanonicalTask,
  TimelineItem,
} from "../api/types";
import {
  getPlanCoordination,
  type PlanCoordinationProjection,
} from "../api/workflowProgress";
import { AppLink } from "../app/router";
import {
  CanonicalId,
  Card,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";
import { WorkflowProgress } from "../components/WorkflowProgress";
import { isCanonicalId } from "../platform/id";
import { usePermissionHint } from "../security/permissions";

export function LiveConnectionStatus({ state }: { state: LiveConnectionState }) {
  return (
    <span
      className={`live live-${state}`}
      role="status"
      aria-live="polite"
      aria-label={`Live updates: ${state}`}
    >
      {state}
    </span>
  );
}

export function TaskDetailPage({
  client,
  taskId,
}: {
  client: ControlPlaneClient;
  taskId: string;
}) {
  const [task, setTask] = useState<CanonicalTask | null>(null);
  const [runs, setRuns] = useState<CanonicalRun[]>([]);
  const [events, setEvents] = useState<TimelineItem[]>([]);
  const [workflow, setWorkflow] = useState<PlanCoordinationProjection | null>(null);
  const [workflowError, setWorkflowError] = useState<unknown>(null);
  const [projects, setProjects] = useState<CanonicalProject[]>([]);
  const [destinationProjectId, setDestinationProjectId] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [liveState, setLiveState] = useState<LiveConnectionState>("connecting");
  const [liveError, setLiveError] = useState<string | null>(null);
  const loadGeneration = useRef(0);
  const permission = usePermissionHint("task:command", taskId);
  const movePermission = usePermissionHint("task:move-project", taskId);

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    if (!isCanonicalId(taskId)) {
      if (generation === loadGeneration.current) {
        setError(new Error("This route does not contain a valid canonical Task ID."));
      }
      return;
    }
    try {
      const [nextTask, nextRuns, timeline] = await Promise.all([
        client.getTask(taskId),
        client.listTaskRuns(taskId, { limit: 100, sort: "created_at", direction: "desc" }),
        client.timeline(taskId, { limit: 100, direction: "asc" }),
      ]);
      if (generation !== loadGeneration.current) return;
      setTask(nextTask);
      setRuns(nextRuns.items);
      setEvents(timeline.items);
      setError(null);

      if (nextTask.plan_ref) {
        try {
          const nextWorkflow = await getPlanCoordination(client, nextTask.plan_ref);
          if (generation !== loadGeneration.current) return;
          if (nextWorkflow.task_id !== taskId || nextWorkflow.id !== nextTask.plan_ref) {
            throw new Error("Control Plane returned a workflow projection for a different Task or Plan.");
          }
          setWorkflow(nextWorkflow);
          setWorkflowError(null);
        } catch (nextWorkflowError) {
          if (generation !== loadGeneration.current) return;
          setWorkflow(null);
          setWorkflowError(nextWorkflowError);
        }
      } else {
        setWorkflow(null);
        setWorkflowError(null);
      }
    } catch (nextError) {
      if (generation === loadGeneration.current) setError(nextError);
    }
  }, [client, taskId]);

  useEffect(() => {
    let active = true;
    void client
      .listProjects({ limit: 100, sort: "name", direction: "asc" })
      .then((page) => {
        if (active) setProjects(page.items);
      })
      .catch(() => {
        if (active) setProjects([]);
      });
    return () => {
      active = false;
    };
  }, [client]);

  useEffect(() => {
    if (task !== null) setDestinationProjectId(task.project_id ?? "");
  }, [task?.project_id]);

  useEffect(() => {
    void load();
    const stream = new TaskEventStream({
      baseUrl: client.baseUrl,
      taskId,
      onEvent: () => {
        setLiveError(null);
        void load();
      },
      onError: (streamError) => setLiveError(describeLiveStreamError(streamError)),
      onState: (state) => {
        setLiveState(state);
        if (state === "open") setLiveError(null);
      },
    });
    stream.open();
    return () => {
      loadGeneration.current += 1;
      stream.close();
    };
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

  const moveTask = async () => {
    if (task === null) return;
    const destination = destinationProjectId || null;
    if (destination === task.project_id) return;
    setBusy(true);
    try {
      await client.moveTaskProject(taskId, destination);
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
  const canMove = !["running", "waiting"].includes(task.status);
  const selectedProjectId = destinationProjectId || null;
  const currentProjectMissing =
    task.project_id !== null && !projects.some((project) => project.id === task.project_id);

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Task</p>
          <h1>{task.title}</h1>
          <CanonicalId value={task.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={task.status} />
          <LiveConnectionStatus state={liveState} />
        </div>
      </header>
      {error != null ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {liveError ? (
        <DegradedState
          title="Live update error"
          detail={`${liveError}. Current Task data remains available through the canonical Control Plane while the stream recovers.`}
        />
      ) : null}
      {permission === "denied" ? (
        <DegradedState
          title="Permission hint"
          detail="The current client hint marks Task commands as denied. The server remains authoritative."
        />
      ) : null}
      <div className="actions" aria-label="Task lifecycle commands">
        {canQueue ? <button disabled={busy} onClick={() => void command("queue")}>Queue</button> : null}
        {canStart ? <button className="primary" disabled={busy} onClick={() => void command("start")}>Start</button> : null}
        {canCancel ? <button disabled={busy} onClick={() => void command("cancel")}>Cancel</button> : null}
        {canRetry ? <button className="primary" disabled={busy} onClick={() => void command("retry")}>Retry</button> : null}
        <button disabled={busy} onClick={() => void load()}>Refresh</button>
      </div>
      <Card title="Project reassignment">
        {movePermission === "denied" ? (
          <DegradedState
            title="Permission hint"
            detail="The current client hint marks Task Project reassignment as denied. The server remains authoritative."
          />
        ) : null}
        <label htmlFor={`task-project-${taskId}`}>Destination Project</label>
        <div className="actions">
          <select
            id={`task-project-${taskId}`}
            value={destinationProjectId}
            disabled={busy || !canMove}
            onChange={(event) => setDestinationProjectId(event.target.value)}
          >
            <option value="">No Project</option>
            {currentProjectMissing ? (
              <option value={task.project_id ?? ""}>{task.project_id} (current)</option>
            ) : null}
            {projects.map((project) => (
              <option key={project.id} value={project.id}>{project.name}</option>
            ))}
          </select>
          <button
            className="primary"
            disabled={busy || !canMove || selectedProjectId === task.project_id}
            onClick={() => void moveTask()}
          >
            Move Task
          </button>
        </div>
        <small>
          This changes the canonical Task Project scope. Historical Events and Runs keep their
          original Project attribution; future execution uses the selected destination.
        </small>
      </Card>
      <div className="grid-two">
        <Card title="Task details">
          <DefinitionList
            values={{
              objective: task.objective,
              revision: task.revision,
              project: task.project_id ?? "—",
              owner: `${task.owner.type}:${task.owner.id}`,
              correlation: task.correlation_id ?? "—",
              updated: formatDate(task.updated_at),
            }}
          />
        </Card>
        <Card title="Canonical references">
          <ReferenceList label="Plan" values={task.plan_ref ? [task.plan_ref] : []} />
          <ReferenceList label="Steps" values={task.step_ids} />
          <ReferenceList label="Artifacts" values={task.artifact_ids} />
          <ReferenceList label="Results" values={task.result_ids} />
        </Card>
      </div>
      <Card title="Durable workflow progress">
        {workflowError != null ? (
          <ErrorState error={workflowError} onRetry={() => void load()} />
        ) : task.plan_ref === null ? (
          <EmptyState title="No active Plan workflow" />
        ) : workflow ? (
          <WorkflowProgress projection={workflow} />
        ) : (
          <LoadingState />
        )}
      </Card>
      <Card title="Runs"><RunTable runs={runs} /></Card>
      <Card title="Timeline">
        {events.length === 0 ? (
          <EmptyState title="No events yet" />
        ) : (
          <ol className="timeline">
            {events.map((event) => (
              <li key={event.id}>
                <div>
                  <strong>{timelineLabel(event)}</strong>
                  <small>{formatDate(timelineTimestamp(event))}</small>
                </div>
                <CanonicalId value={event.id} />
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}

function RunTable({ runs }: { runs: CanonicalRun[] }) {
  if (runs.length === 0) return <EmptyState title="No runs" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Run</th><th>Status</th><th>Task</th><th>Attempt</th></tr></thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td><AppLink href={`/runs/${run.id}`}><CanonicalId value={run.id} /></AppLink></td>
              <td><StatusBadge value={run.status} /></td>
              <td><AppLink href={`/tasks/${run.task_id}`}><CanonicalId value={run.task_id} /></AppLink></td>
              <td>{run.attempt}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReferenceList({ label, values }: { label: string; values: string[] }) {
  return (
    <div className="reference-group">
      <strong>{label}</strong>
      {values.length ? <ul>{values.map((value) => <li key={value}><CanonicalId value={value} /></li>)}</ul> : <span>—</span>}
    </div>
  );
}

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return <dl>{Object.entries(values).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function timelineLabel(item: TimelineItem): string {
  return item.type === "event" ? item.event_type : `${item.component}: ${item.event_name}`;
}

function timelineTimestamp(item: TimelineItem): string {
  return item.type === "event" ? item.occurred_at : item.timestamp;
}
