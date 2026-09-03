import { useCallback, useEffect, useMemo, useState } from "react";
import { ControlPlaneClient } from "../api/client";
import {
  isTelemetryEntry,
  summarizeTimeline,
  timelineContext,
  timelineName,
  timelineTimestamp,
} from "../api/observability";
import type { CanonicalTask, Page, TimelineItem } from "../api/types";
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

export function ObservabilityPage({
  client,
  view,
}: {
  client: ControlPlaneClient;
  view: "events" | "observability";
}) {
  const [tasks, setTasks] = useState<Page<CanonicalTask> | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [timeline, setTimeline] = useState<TimelineItem[] | null>(null);
  const [taskError, setTaskError] = useState<unknown>(null);
  const [timelineError, setTimelineError] = useState<unknown>(null);

  const loadTasks = useCallback(async () => {
    try {
      const next = await client.listTasks({ limit: 50, sort: "updated_at", direction: "desc" });
      setTasks(next);
      setTaskError(null);
      setSelectedTaskId((current) => {
        if (current && next.items.some((task) => task.id === current)) return current;
        return next.items[0]?.id ?? "";
      });
    } catch (error) {
      setTaskError(error);
    }
  }, [client]);

  const loadTimeline = useCallback(async () => {
    if (!selectedTaskId) {
      setTimeline([]);
      setTimelineError(null);
      return;
    }
    try {
      const next = await client.timeline(selectedTaskId, { limit: 200, direction: "asc" });
      setTimeline(next.items);
      setTimelineError(null);
    } catch (error) {
      setTimelineError(error);
    }
  }, [client, selectedTaskId]);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  useEffect(() => {
    setTimeline(null);
    void loadTimeline();
  }, [loadTimeline]);

  const selectedTask = tasks?.items.find((task) => task.id === selectedTaskId) ?? null;
  const summary = useMemo(() => summarizeTimeline(timeline ?? []), [timeline]);
  const heading = view === "events" ? "Events & activity" : "Observability";

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical Task timeline</p>
        <h1>{heading}</h1>
        <p>Task-scoped domain events and derived #16 telemetry read only through the versioned Control Plane.</p>
      </header>

      <DegradedState
        title="Task-scoped view"
        detail="The current northbound observability contract enriches each canonical Task timeline. This page intentionally does not invent a global event store endpoint or read observability backends directly."
      />

      {taskError ? <ErrorState error={taskError} onRetry={() => void loadTasks()} /> : null}
      <Card title="Task scope">
        {!tasks ? (
          <LoadingState />
        ) : tasks.items.length === 0 ? (
          <EmptyState title="No Tasks available" />
        ) : (
          <div className="toolbar">
            <label>
              Task
              <select value={selectedTaskId} onChange={(event) => setSelectedTaskId(event.target.value)}>
                {tasks.items.map((task) => (
                  <option key={task.id} value={task.id}>{task.title} — {task.id}</option>
                ))}
              </select>
            </label>
            <button onClick={() => void loadTimeline()}>Refresh timeline</button>
          </div>
        )}
        {selectedTask ? <p><AppLink href={`/tasks/${selectedTask.id}`}>Open Task</AppLink> · <CanonicalId value={selectedTask.id} /></p> : null}
      </Card>

      <div className="metrics">
        <Metric label="Timeline entries" value={timeline ? summary.total : "—"} />
        <Metric label="Domain events" value={timeline ? summary.domainEvents : "—"} />
        <Metric label="Telemetry" value={timeline ? summary.telemetryEntries : "—"} />
        <Metric label="Failures" value={timeline ? summary.failures : "—"} />
        <Metric label="Components" value={timeline ? summary.components.length : "—"} />
      </div>

      {timelineError ? <ErrorState error={timelineError} onRetry={() => void loadTimeline()} /> : null}
      <Card title="Timeline">
        {timeline === null ? <LoadingState /> : timeline.length === 0 ? <EmptyState title="No timeline entries" /> : <TimelineTable items={timeline} />}
      </Card>

      {summary.components.length > 0 ? (
        <Card title="Observed components">
          <div className="actions">{summary.components.map((component) => <StatusBadge key={component} value={component} />)}</div>
        </Card>
      ) : null}
    </div>
  );
}

function TimelineTable({ items }: { items: TimelineItem[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Time</th><th>Entry</th><th>Kind</th><th>Context</th><th>Outcome</th><th>Duration</th></tr></thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{formatDate(timelineTimestamp(item))}</td>
              <td><strong>{timelineName(item)}</strong><div><CanonicalId value={item.id} /></div></td>
              <td><StatusBadge value={item.type === "event" ? "domain event" : "telemetry"} /></td>
              <td>{timelineContext(item)}</td>
              <td>{isTelemetryEntry(item) ? <StatusBadge value={item.outcome} /> : "—"}</td>
              <td>{isTelemetryEntry(item) ? formatDuration(item.duration_seconds) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatDuration(value: number | null): string {
  if (value === null) return "—";
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  return `${value.toFixed(2)} s`;
}
