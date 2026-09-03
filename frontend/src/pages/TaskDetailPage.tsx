import { useCallback, useEffect, useState } from "react";
import { ControlPlaneClient } from "../api/client";
import {
  describeLiveStreamError,
  TaskEventStream,
  type LiveConnectionState,
} from "../api/live";
import type { CanonicalRun, CanonicalTask, TimelineItem } from "../api/types";
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
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [liveState, setLiveState] = useState<LiveConnectionState>("connecting");
  const [liveError, setLiveError] = useState<string | null>(null);
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
