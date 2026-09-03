import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { ControlPlaneClient } from "../api/client";
import type {
  CanonicalTask,
  Page,
  TaskDependencyKind,
  TaskManagementChanges,
  TaskPriority,
  TaskResponsibilityKind,
} from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink, useRouter } from "../app/router";
import { PaginationControls } from "../components/Pagination";
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

const PRIORITIES: TaskPriority[] = ["low", "normal", "high", "urgent"];
const STATUSES = ["draft", "ready", "running", "waiting", "succeeded", "failed", "cancelled"];
type DeadlineView = "" | "overdue" | "24h" | "7d" | "30d";
type AssignmentState = "" | "assigned" | "unassigned";
type AgentKindFilter = "" | "agent" | "agent_team";
const DEADLINE_WINDOW_HOURS: Record<Exclude<DeadlineView, "" | "overdue">, number> = {
  "24h": 24,
  "7d": 24 * 7,
  "30d": 24 * 30,
};

export function ManagedTasksPage({ client }: { client: ControlPlaneClient }) {
  const [page, setPage] = useState<Page<CanonicalTask> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [projectId, setProjectId] = useState("");
  const [assignmentState, setAssignmentState] = useState<AssignmentState>("");
  const [responsibleKind, setResponsibleKind] = useState<"" | TaskResponsibilityKind>("");
  const [responsibleId, setResponsibleId] = useState("");
  const [agentKind, setAgentKind] = useState<AgentKindFilter>("");
  const [agentId, setAgentId] = useState("");
  const [blocked, setBlocked] = useState(false);
  const [deadlineView, setDeadlineView] = useState<DeadlineView>("");
  const [sort, setSort] = useState("priority");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkPriority, setBulkPriority] = useState<TaskPriority>("high");
  const { navigate } = useRouter();

  const filters = useMemo(() => {
    const next: Record<string, string> = {};
    if (status) next.status = status;
    if (priority) next.priority = priority;
    if (projectId.trim()) next.project_id = projectId.trim();
    if (assignmentState) next.assignment_state = assignmentState;
    if (responsibleKind) next.responsible_type = responsibleKind;
    if (responsibleId.trim()) next.responsible_id = responsibleId.trim();
    if (agentKind) next.agent_assignment_type = agentKind;
    if (agentId.trim()) next.agent_assignment_id = agentId.trim();
    if (blocked) next.blocked = "true";
    if (deadlineView === "overdue") {
      next.overdue = "true";
    } else if (deadlineView) {
      const now = new Date();
      const until = new Date(now.getTime() + DEADLINE_WINDOW_HOURS[deadlineView] * 60 * 60 * 1000);
      next.due_after = now.toISOString();
      next.due_before = until.toISOString();
    }
    return next;
  }, [
    agentId,
    agentKind,
    assignmentState,
    blocked,
    deadlineView,
    priority,
    projectId,
    responsibleId,
    responsibleKind,
    status,
  ]);

  const queryKey = useMemo(
    () => JSON.stringify({ filters, sort, direction }),
    [direction, filters, sort],
  );
  const pagination = useCursorPagination(queryKey);

  const load = useCallback(async () => {
    setError(null);
    try {
      setPage(
        await client.listTasks({
          limit: 100,
          cursor: pagination.cursor,
          sort,
          direction,
          filters: Object.keys(filters).length > 0 ? filters : undefined,
        }),
      );
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, direction, filters, pagination.cursor, sort]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setSelected(new Set());
  }, [queryKey]);

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreating(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    const due = localDateTimeToIso(String(form.get("due_at") ?? ""));
    const responsible = String(form.get("responsible_id") ?? "").trim();
    try {
      const task = await client.createTask({
        title: String(form.get("title") ?? ""),
        objective: String(form.get("objective") ?? ""),
        owner_type: "user",
        owner_id: String(form.get("owner_id") ?? "local"),
        project_id: optionalText(form, "project_id") ?? undefined,
        priority: String(form.get("priority") ?? "normal") as TaskPriority,
        due_at: due,
        deadline_timezone: due ? browserTimezone() : null,
        responsibility: responsible
          ? {
              kind: String(form.get("responsible_kind") ?? "user") as TaskResponsibilityKind,
              id: responsible,
            }
          : null,
      });
      navigate(`/tasks/${task.id}/manage`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setCreating(false);
    }
  };

  const toggleSelected = (taskId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  };

  const applyBulk = async (changes: TaskManagementChanges) => {
    if (selected.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      await client.bulkUpdateTaskManagement(
        Array.from(selected, (taskId) => ({ task_id: taskId, changes })),
      );
      setSelected(new Set());
      await load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical work management</p>
        <h1>Task queue</h1>
        <p>Priority, timing, responsibility and blockers projected directly from canonical Task metadata.</p>
      </header>
      {error != null && <ErrorState error={error} onRetry={() => void load()} />}
      <Card title="Create managed task">
        <form className="form-grid" onSubmit={create}>
          <label>Title<input name="title" required /></label>
          <label>Objective<input name="objective" required /></label>
          <label>Owner ID<input name="owner_id" defaultValue="local" required /></label>
          <label>Project ID<input name="project_id" placeholder="project_…" /></label>
          <label>Priority<select name="priority" defaultValue="normal">{PRIORITIES.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Due<input name="due_at" type="datetime-local" /></label>
          <label>Responsible type<select name="responsible_kind" defaultValue="user"><option value="user">user</option><option value="team">team</option><option value="organization">organization</option></select></label>
          <label>Responsible ID<input name="responsible_id" placeholder="optional" /></label>
          <button className="primary" disabled={creating}>{creating ? "Creating…" : "Create task"}</button>
        </form>
      </Card>
      <Card title="Queue filters and ordering">
        <div className="toolbar">
          <label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">all</option>{STATUSES.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Priority<select value={priority} onChange={(event) => setPriority(event.target.value)}><option value="">all</option>{PRIORITIES.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Project<input value={projectId} onChange={(event) => setProjectId(event.target.value)} placeholder="project_…" /></label>
          <label>Assignment<select value={assignmentState} onChange={(event) => setAssignmentState(event.target.value as AssignmentState)}><option value="">all</option><option value="assigned">assigned</option><option value="unassigned">unassigned</option></select></label>
          <label>Responsible type<select value={responsibleKind} onChange={(event) => setResponsibleKind(event.target.value as "" | TaskResponsibilityKind)}><option value="">all</option><option value="user">user</option><option value="team">team</option><option value="organization">organization</option></select></label>
          <label>Responsible ID<input value={responsibleId} onChange={(event) => setResponsibleId(event.target.value)} placeholder="ID" /></label>
          <label>Agent kind<select value={agentKind} onChange={(event) => setAgentKind(event.target.value as AgentKindFilter)}><option value="">all</option><option value="agent">agent</option><option value="agent_team">agent team</option></select></label>
          <label>Agent / team ID<input value={agentId} onChange={(event) => setAgentId(event.target.value)} placeholder="agent_… / team_…" /></label>
          <label>Deadline<select value={deadlineView} onChange={(event) => setDeadlineView(event.target.value as DeadlineView)}><option value="">all</option><option value="overdue">overdue</option><option value="24h">due in 24h</option><option value="7d">due in 7 days</option><option value="30d">due in 30 days</option></select></label>
          <label><input type="checkbox" checked={blocked} onChange={(event) => setBlocked(event.target.checked)} /> blocked</label>
          <label>Sort<select value={sort} onChange={(event) => setSort(event.target.value)}><option value="priority">priority</option><option value="due">due</option><option value="updated_at">updated</option><option value="status">status</option></select></label>
          <label>Direction<select value={direction} onChange={(event) => setDirection(event.target.value as "asc" | "desc")}><option value="desc">descending</option><option value="asc">ascending</option></select></label>
        </div>
      </Card>
      <Card title={`Bulk operations · ${selected.size} selected`}>
        <div className="toolbar">
          <label>Priority<select value={bulkPriority} onChange={(event) => setBulkPriority(event.target.value as TaskPriority)}>{PRIORITIES.map((value) => <option key={value}>{value}</option>)}</select></label>
          <button disabled={busy || selected.size === 0} onClick={() => void applyBulk({ priority: bulkPriority })}>Set priority</button>
          <button disabled={busy || selected.size === 0} onClick={() => void applyBulk({ archived: true })}>Archive</button>
          <button disabled={busy || selected.size === 0} onClick={() => void applyBulk({ archived: false })}>Unarchive</button>
        </div>
      </Card>
      {!page ? <LoadingState /> : (
        <>
          <ManagedTaskTable tasks={page.items} selected={selected} onToggle={toggleSelected} />
          <PaginationControls
            page={page}
            pageNumber={pagination.pageNumber}
            hasPrevious={pagination.hasPrevious}
            onPrevious={pagination.previous}
            onRefresh={() => void load()}
            onNext={() => pagination.next(page.next_cursor)}
          />
        </>
      )}
    </div>
  );
}

export function TaskManagementDetailPage({ client, taskId }: { client: ControlPlaneClient; taskId: string }) {
  const [task, setTask] = useState<CanonicalTask | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!isCanonicalId(taskId)) {
      setError(new Error("This route does not contain a valid canonical Task ID."));
      return;
    }
    try {
      setTask(await client.getTask(taskId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, taskId]);

  useEffect(() => {
    void load();
  }, [load]);

  const update = async (changes: TaskManagementChanges) => {
    setBusy(true);
    setError(null);
    try {
      setTask(await client.updateTaskManagement(taskId, changes));
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const submitMetadata = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!task) return;
    const form = new FormData(event.currentTarget);
    const responsibilityId = optionalText(form, "responsibility_id");
    const agentId = optionalText(form, "agent_id");
    const due = localDateTimeToIso(String(form.get("due_at") ?? ""));
    const notBefore = localDateTimeToIso(String(form.get("not_before") ?? ""));
    const effortText = String(form.get("effort_hint") ?? "").trim();
    const revisionText = String(form.get("agent_revision") ?? "").trim();
    await update({
      priority: String(form.get("priority") ?? task.priority) as TaskPriority,
      due_at: due,
      deadline_timezone: due ? optionalText(form, "deadline_timezone") ?? browserTimezone() : null,
      not_before: notBefore,
      responsibility: responsibilityId
        ? {
            kind: String(form.get("responsibility_kind") ?? "user") as TaskResponsibilityKind,
            id: responsibilityId,
          }
        : null,
      agent_assignment: agentId
        ? {
            kind: String(form.get("agent_kind") ?? "agent") as "agent" | "agent_team",
            id: agentId,
            revision: revisionText ? Number(revisionText) : null,
            required: form.get("agent_required") === "on",
            policy_ref: optionalText(form, "agent_policy"),
          }
        : null,
      labels: String(form.get("labels") ?? "").split(",").map((value) => value.trim()).filter(Boolean),
      workspace_id: optionalText(form, "workspace_id"),
      parent_task_id: optionalText(form, "parent_task_id"),
      blocking_reason: optionalText(form, "blocking_reason"),
      effort_hint: effortText ? Number(effortText) : null,
      archived: form.get("archived") === "on",
      hidden: form.get("hidden") === "on",
    });
  };

  const addDependency = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!task) return;
    const form = new FormData(event.currentTarget);
    const dependencyId = String(form.get("dependency_id") ?? "").trim();
    if (!dependencyId) return;
    const kind = String(form.get("dependency_kind") ?? "depends_on") as TaskDependencyKind;
    const dependencies = [
      ...task.dependencies.filter((dependency) => dependency.task_id !== dependencyId),
      { task_id: dependencyId, kind },
    ];
    await update({ dependencies });
    event.currentTarget.reset();
  };

  const removeDependency = async (dependencyId: string) => {
    if (!task) return;
    await update({
      dependencies: task.dependencies.filter((dependency) => dependency.task_id !== dependencyId),
    });
  };

  if (error && !task) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!task) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Task management</p>
          <h1>{task.title}</h1>
          <CanonicalId value={task.id} />
        </div>
        <div className="detail-status"><StatusBadge value={task.priority} /><StatusBadge value={task.status} /></div>
      </header>
      {error != null && <ErrorState error={error} onRetry={() => void load()} />}
      {task.overdue && <DegradedState title="Overdue" detail={`Deadline passed${task.due_at ? ` at ${formatDate(task.due_at)}` : ""}. This does not change lifecycle state.`} />}
      {task.blocked && <DegradedState title="Blocked" detail={task.effective_blocking_reason ?? task.wait_reason ?? "The canonical Task projection reports a blocker."} />}
      <div className="actions">
        <AppLink href={`/tasks/${task.id}`}>Execution detail</AppLink>
        <button disabled={busy} onClick={() => void load()}>Refresh</button>
      </div>
      <div className="grid-two">
        <Card title="Planning metadata">
          <form className="form-grid" key={`${task.id}:${task.revision}:${task.updated_at}`} onSubmit={(event) => void submitMetadata(event)}>
            <label>Priority<select name="priority" defaultValue={task.priority}>{PRIORITIES.map((value) => <option key={value}>{value}</option>)}</select></label>
            <label>Due<input name="due_at" type="datetime-local" defaultValue={isoToLocalInput(task.due_at)} /></label>
            <label>Deadline timezone<input name="deadline_timezone" defaultValue={task.deadline_timezone ?? browserTimezone()} /></label>
            <label>Not before<input name="not_before" type="datetime-local" defaultValue={isoToLocalInput(task.not_before)} /></label>
            <label>Responsibility<select name="responsibility_kind" defaultValue={task.responsibility?.kind ?? "user"}><option value="user">user</option><option value="team">team</option><option value="organization">organization</option></select></label>
            <label>Responsible ID<input name="responsibility_id" defaultValue={task.responsibility?.id ?? ""} /></label>
            <label>Agent kind<select name="agent_kind" defaultValue={task.agent_assignment?.kind ?? "agent"}><option value="agent">agent</option><option value="agent_team">agent team</option></select></label>
            <label>Agent / team ID<input name="agent_id" defaultValue={task.agent_assignment?.id ?? ""} placeholder="agent_… / team_…" /></label>
            <label>Agent revision<input name="agent_revision" type="number" min="1" defaultValue={task.agent_assignment?.revision ?? ""} /></label>
            <label>Assignment policy<input name="agent_policy" defaultValue={task.agent_assignment?.policy_ref ?? ""} /></label>
            <label><input name="agent_required" type="checkbox" defaultChecked={task.agent_assignment?.required ?? false} /> required assignment</label>
            <label>Labels<input name="labels" defaultValue={task.labels.join(", ")} /></label>
            <label>Workspace ID<input name="workspace_id" defaultValue={task.workspace_id ?? ""} /></label>
            <label>Parent Task ID<input name="parent_task_id" defaultValue={task.parent_task_id ?? ""} /></label>
            <label>Blocking reason<input name="blocking_reason" defaultValue={task.blocking_reason ?? ""} /></label>
            <label>Effort hint<input name="effort_hint" type="number" min="0" step="0.1" defaultValue={task.effort_hint ?? ""} /></label>
            <label><input name="archived" type="checkbox" defaultChecked={task.archived} /> archived</label>
            <label><input name="hidden" type="checkbox" defaultChecked={task.hidden} /> hidden</label>
            <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save planning metadata"}</button>
          </form>
        </Card>
        <Card title="Derived queue state">
          <dl className="definition-list">
            <dt>Eligible</dt><dd>{String(task.eligible)}</dd>
            <dt>Overdue</dt><dd>{String(task.overdue)}</dd>
            <dt>Not-before blocked</dt><dd>{String(task.not_before_blocked)}</dd>
            <dt>Management blocked</dt><dd>{String(task.management_blocked)}</dd>
            <dt>Effective blocker</dt><dd>{task.effective_blocking_reason ?? "—"}</dd>
            <dt>Responsible</dt><dd>{task.responsible_id ? `${task.responsible_type}:${task.responsible_id}` : "unassigned"}</dd>
            <dt>Agent assignment</dt><dd>{task.agent_assignment_id ? `${task.agent_assignment_type}:${task.agent_assignment_id}` : "unassigned"}</dd>
            <dt>Project</dt><dd>{task.project_id ?? "—"}</dd>
          </dl>
        </Card>
      </div>
      <Card title="Dependencies and blockers">
        {task.dependencies.length === 0 ? <EmptyState title="No Task dependencies" /> : (
          <div className="table-wrap"><table><thead><tr><th>Task</th><th>Relation</th><th>State</th><th /></tr></thead><tbody>{task.dependencies.map((dependency) => {
            const failed = task.failed_dependency_ids.includes(dependency.task_id);
            const blocking = task.blocking_task_ids.includes(dependency.task_id);
            return <tr key={`${dependency.kind}:${dependency.task_id}`}><td><AppLink href={`/tasks/${dependency.task_id}`}>{dependency.task_id}</AppLink></td><td>{dependency.kind}</td><td>{failed ? <StatusBadge value="failed" /> : blocking ? <StatusBadge value="blocked" /> : <StatusBadge value="satisfied" />}</td><td><button disabled={busy} onClick={() => void removeDependency(dependency.task_id)}>Remove</button></td></tr>;
          })}</tbody></table></div>
        )}
        <form className="toolbar" onSubmit={(event) => void addDependency(event)}>
          <label>Task ID<input name="dependency_id" placeholder="task_…" required /></label>
          <label>Relation<select name="dependency_kind" defaultValue="depends_on"><option value="depends_on">depends on</option><option value="related_to">related to</option></select></label>
          <button disabled={busy}>Add relation</button>
        </form>
      </Card>
    </div>
  );
}

function ManagedTaskTable({
  tasks,
  selected,
  onToggle,
}: {
  tasks: CanonicalTask[];
  selected: Set<string>;
  onToggle: (taskId: string) => void;
}) {
  if (tasks.length === 0) return <EmptyState title="No tasks match this queue" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Select</th><th>Priority</th><th>Task</th><th>Status</th><th>Due</th><th>Responsible</th><th>Blockers</th><th>Updated</th></tr></thead>
        <tbody>{tasks.map((task) => <tr key={task.id}>
          <td><input aria-label={`Select ${task.title}`} type="checkbox" checked={selected.has(task.id)} onChange={() => onToggle(task.id)} /></td>
          <td><StatusBadge value={task.priority} />{task.overdue && <div><StatusBadge value="overdue" /></div>}</td>
          <td><AppLink href={`/tasks/${task.id}`}>{task.title}</AppLink><div><CanonicalId value={task.id} /></div><div><AppLink href={`/tasks/${task.id}/manage`}>Manage</AppLink></div></td>
          <td><StatusBadge value={task.status} /></td>
          <td>{task.due_at ? formatDate(task.due_at) : "—"}</td>
          <td>{task.responsible_id ? `${task.responsible_type}:${task.responsible_id}` : task.agent_assignment_id ? `${task.agent_assignment_type}:${task.agent_assignment_id}` : "unassigned"}</td>
          <td>{task.blocked ? <><StatusBadge value="blocked" /><div>{task.effective_blocking_reason ?? "blocked"}</div></> : "—"}</td>
          <td>{formatDate(task.updated_at)}</td>
        </tr>)}</tbody>
      </table>
    </div>
  );
}

function optionalText(form: FormData, field: string): string | null {
  const value = String(form.get(field) ?? "").trim();
  return value || null;
}

function localDateTimeToIso(value: string): string | null {
  if (!value.trim()) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function isoToLocalInput(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}