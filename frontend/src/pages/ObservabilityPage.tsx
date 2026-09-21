import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
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
import { TraceExplorer } from "./observability/TraceExplorer";

export function ObservabilityPage({
  client,
  view,
  initialTaskId = "",
}: {
  client: ControlPlaneClient;
  view: "events" | "observability";
  initialTaskId?: string;
}) {
  const [tasks, setTasks] = useState<Page<CanonicalTask> | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState(initialTaskId);
  const [taskIdDraft, setTaskIdDraft] = useState(initialTaskId);
  const [timeline, setTimeline] = useState<TimelineItem[] | null>(null);
  const [timelineNextCursor, setTimelineNextCursor] = useState<string | null>(null);
  const [timelineTotal, setTimelineTotal] = useState<number | null>(null);
  const [taskError, setTaskError] = useState<unknown>(null);
  const [timelineError, setTimelineError] = useState<unknown>(null);
  const taskScopeEditedRef = useRef(Boolean(initialTaskId));

  const loadTasks = useCallback(async () => {
    try {
      const next = await client.listTasks({
        limit: 50,
        sort: "updated_at",
        direction: "desc",
      });
      setTasks(next);
      setTaskError(null);
      const recentTaskId = next.items[0]?.id || "";
      setSelectedTaskId((current) =>
        preserveTaskScopeDuringRecentTaskLoad(
          current,
          recentTaskId,
          taskScopeEditedRef.current,
        ),
      );
      setTaskIdDraft((current) =>
        preserveTaskScopeDuringRecentTaskLoad(
          current,
          recentTaskId,
          taskScopeEditedRef.current,
        ),
      );
    } catch (error) {
      setTaskError(error);
    }
  }, [client]);

  const loadTimeline = useCallback(async (cursor?: string, append = false) => {
    if (!selectedTaskId) {
      setTimeline([]);
      setTimelineNextCursor(null);
      setTimelineTotal(0);
      setTimelineError(null);
      return;
    }
    try {
      const next = await client.timeline(selectedTaskId, {
        limit: 100,
        cursor,
        direction: "asc",
      });
      setTimeline((current) => append && current ? [...current, ...next.items] : next.items);
      setTimelineNextCursor(next.next_cursor);
      setTimelineTotal(next.total);
      setTimelineError(null);
    } catch (error) {
      setTimelineError(error);
    }
  }, [client, selectedTaskId]);

  useEffect(() => {
    taskScopeEditedRef.current = Boolean(initialTaskId);
    setSelectedTaskId(initialTaskId);
    setTaskIdDraft(initialTaskId);
  }, [initialTaskId]);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  useEffect(() => {
    setTimeline(null);
    setTimelineNextCursor(null);
    setTimelineTotal(null);
    void loadTimeline();
  }, [loadTimeline]);

  const selectedTask =
    tasks?.items.find((task) => task.id === selectedTaskId) ?? null;
  const summary = useMemo(() => summarizeTimeline(timeline ?? []), [timeline]);
  const heading = view === "events" ? "Events & activity" : "Observability";

  const applyTaskId = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    taskScopeEditedRef.current = true;
    setSelectedTaskId(taskIdDraft.trim());
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical Task telemetry</p>
        <h1>{heading}</h1>
        <p>
          Task-scoped lifecycle activity and derived #16 telemetry read only through the
          versioned Control Plane.
        </p>
      </header>

      <DegradedState
        title="Task-scoped view"
        detail="The trace explorer is a read projection over canonical spans, timeline events and #76 accounting. It does not create a second lifecycle or telemetry authority."
      />

      {taskError ? (
        <ErrorState error={taskError} onRetry={() => void loadTasks()} />
      ) : null}
      <Card title="Task scope">
        <form className="toolbar" onSubmit={applyTaskId}>
          <label>
            Exact Task ID
            <input
              value={taskIdDraft}
              onChange={(event) => {
                taskScopeEditedRef.current = true;
                setTaskIdDraft(event.target.value);
              }}
              placeholder="task_…"
            />
          </label>
          <button type="submit" disabled={!taskIdDraft.trim()}>Open telemetry</button>
          <button type="button" onClick={() => void loadTimeline()}>Refresh</button>
        </form>
        {!tasks ? (
          <LoadingState label="Loading recent Tasks…" />
        ) : tasks.items.length === 0 ? (
          <EmptyState title="No recent Tasks available" />
        ) : (
          <label>
            Recent Task
            <select
              value={tasks.items.some((task) => task.id === selectedTaskId) ? selectedTaskId : ""}
              onChange={(event) => {
                taskScopeEditedRef.current = true;
                setSelectedTaskId(event.target.value);
                setTaskIdDraft(event.target.value);
              }}
            >
              <option value="">Choose a recent Task</option>
              {tasks.items.map((task) => (
                <option key={task.id} value={task.id}>
                  {task.title} — {task.id}
                </option>
              ))}
            </select>
          </label>
        )}
        {selectedTaskId ? (
          <p>
            <AppLink href={`/tasks/${selectedTaskId}`}>{selectedTask ? "Open Task" : "Inspect Task"}</AppLink> ·{" "}
            <AppLink href={`/${view}/${selectedTaskId}`}>Permalink</AppLink> ·{" "}
            <CanonicalId value={selectedTaskId} />
          </p>
        ) : null}
      </Card>

      {view === "observability" ? (
        <TraceExplorer client={client} taskId={selectedTaskId} />
      ) : null}

      <div className="metrics">
        <Metric
          label="Timeline entries"
          value={timeline ? `${summary.total} loaded / ${timelineTotal ?? summary.total} total` : "—"}
        />
        <Metric label="Domain events" value={timeline ? summary.domainEvents : "—"} />
        <Metric label="Telemetry" value={timeline ? summary.telemetryEntries : "—"} />
        <Metric label="Failures" value={timeline ? summary.failures : "—"} />
        <Metric
          label="Components"
          value={timeline ? summary.components.length : "—"}
        />
      </div>

      {timelineError ? (
        <ErrorState error={timelineError} onRetry={() => void loadTimeline()} />
      ) : null}
      <Card title="Timeline">
        {timeline === null ? (
          <LoadingState />
        ) : timeline.length === 0 ? (
          <EmptyState title="No timeline entries" />
        ) : (
          <TimelineTable items={timeline} />
        )}
        {timelineNextCursor ? (
          <button
            type="button"
            onClick={() => void loadTimeline(timelineNextCursor, true)}
          >
            Load more timeline entries
          </button>
        ) : null}
      </Card>

      {summary.components.length > 0 ? (
        <Card title="Observed components">
          <div className="actions">
            {summary.components.map((component) => (
              <StatusBadge key={component} value={component} />
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function TimelineTable({ items }: { items: TimelineItem[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Entry</th>
            <th>Kind</th>
            <th>Context</th>
            <th>Outcome</th>
            <th>Duration</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{formatDate(timelineTimestamp(item))}</td>
              <td>
                <strong>{timelineName(item)}</strong>
                <div>
                  <CanonicalId value={item.id} />
                </div>
              </td>
              <td>
                <StatusBadge
                  value={item.type === "event" ? "domain event" : "telemetry"}
                />
              </td>
              <td>{timelineContext(item)}</td>
              <td>
                {isTelemetryEntry(item) ? (
                  <StatusBadge value={item.outcome} />
                ) : (
                  "—"
                )}
              </td>
              <td>
                {isTelemetryEntry(item)
                  ? formatDuration(item.duration_seconds)
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
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


export function preserveTaskScopeDuringRecentTaskLoad(
  current: string,
  recentTaskId: string,
  edited: boolean,
): string {
  if (current || edited) return current;
  return recentTaskId;
}
