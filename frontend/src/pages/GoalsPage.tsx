import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  GoalClient,
  type CanonicalGoal,
  type CanonicalGoalConstraints,
  type CanonicalGoalCriterion,
  type GoalEvidenceInput,
  type GoalTaskRevisionPolicy,
} from "../api/goals";
import type { JsonValue, Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  Card,
  CanonicalId,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const TERMINAL_GOAL_STATES = new Set(["satisfied", "failed", "cancelled", "superseded"]);
const DEFAULT_CRITERIA = JSON.stringify(
  [
    {
      criterion_id: "acceptance",
      kind: "human_acceptance",
      description: "A human explicitly accepts the Goal outcome",
      operator: "truthy",
      target: true,
      required: true,
    },
  ],
  null,
  2,
);

export function GoalsPage({ client }: { client: GoalClient }) {
  const [page, setPage] = useState<Page<CanonicalGoal> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [createError, setCreateError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [projectId, setProjectId] = useState("");
  const [criteriaJson, setCriteriaJson] = useState(DEFAULT_CRITERIA);
  const pagination = useCursorPagination("goals:id:asc");

  const load = useCallback(async () => {
    try {
      setPage(
        await client.list({
          limit: 50,
          cursor: pagination.cursor,
          sort: "id",
          direction: "asc",
        }),
      );
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, pagination.cursor]);

  useEffect(() => {
    void load();
  }, [load]);

  async function submitCreate(event: FormEvent) {
    event.preventDefault();
    setCreating(true);
    try {
      const successCriteria = parseCriteria(criteriaJson);
      await client.create({
        title: requireText(title, "title"),
        objective: requireText(objective, "objective"),
        project_id: projectId.trim() || undefined,
        success_criteria: successCriteria,
      });
      setTitle("");
      setObjective("");
      setProjectId("");
      setCriteriaJson(DEFAULT_CRITERIA);
      setCreateError(null);
      await load();
    } catch (nextError) {
      setCreateError(nextError);
    } finally {
      setCreating(false);
    }
  }

  const active = page?.items.filter((goal) => goal.status === "active" || goal.status === "waiting").length ?? "—";
  const paused = page?.items.filter((goal) => goal.status === "paused").length ?? "—";
  const terminal = page?.items.filter((goal) => TERMINAL_GOAL_STATES.has(goal.status)).length ?? "—";

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Durable continuous objectives</p>
        <h1>Goals</h1>
        <p>
          Goals own long-lived objective state above canonical Tasks. Progress comes from explicit
          criteria and evidence; executable work remains in the normal Task → Plan → Run lifecycle.
        </p>
      </header>

      <div className="metrics">
        <Metric label="Goals" value={page?.total ?? "—"} />
        <Metric label="Active / waiting on page" value={active} />
        <Metric label="Paused on page" value={paused} />
        <Metric label="Terminal on page" value={terminal} />
      </div>

      <Card title="Goal inventory">
        <div className="actions">
          <button onClick={() => void load()}>Refresh</button>
        </div>
        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
        {!page && !error ? <LoadingState /> : null}
        {page ? <GoalTable goals={page.items} /> : null}
        {page ? (
          <PaginationControls
            page={page}
            pageNumber={pagination.pageNumber}
            hasPrevious={pagination.hasPrevious}
            onPrevious={pagination.previous}
            onRefresh={() => void load()}
            onNext={() => pagination.next(page.next_cursor)}
          />
        ) : null}
      </Card>

      <Card title="Create Goal">
        <p>
          Creation produces a draft Goal. Activation is explicit so success criteria, scope and
          constraints can be inspected before continuous work begins.
        </p>
        {createError ? <ErrorState error={createError} /> : null}
        <form className="stack" onSubmit={(event) => void submitCreate(event)}>
          <div className="form-grid">
            <label>
              Title
              <input required value={title} onChange={(event) => setTitle(event.target.value)} />
            </label>
            <label>
              Project ID
              <input value={projectId} onChange={(event) => setProjectId(event.target.value)} />
            </label>
          </div>
          <label>
            Objective
            <textarea required rows={3} value={objective} onChange={(event) => setObjective(event.target.value)} />
          </label>
          <label>
            Success criteria JSON
            <textarea rows={12} value={criteriaJson} onChange={(event) => setCriteriaJson(event.target.value)} />
          </label>
          <div className="actions">
            <button disabled={creating} type="submit">{creating ? "Creating…" : "Create draft Goal"}</button>
          </div>
        </form>
      </Card>
    </div>
  );
}

export function GoalDetailPage({ client, goalId }: { client: GoalClient; goalId: string }) {
  const [goal, setGoal] = useState<CanonicalGoal | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [cancelReason, setCancelReason] = useState("Cancelled by operator");
  const [failureReason, setFailureReason] = useState("Goal can no longer be achieved under the current objective and constraints");
  const [taskId, setTaskId] = useState("");
  const [revisionTitle, setRevisionTitle] = useState("");
  const [revisionObjective, setRevisionObjective] = useState("");
  const [activeTaskPolicy, setActiveTaskPolicy] = useState<GoalTaskRevisionPolicy>("retain");
  const [reopenTerminal, setReopenTerminal] = useState(false);
  const [reviewTrigger, setReviewTrigger] = useState("manual:web");
  const [evidenceJson, setEvidenceJson] = useState("[]");
  const [nextReviewAt, setNextReviewAt] = useState("");

  const load = useCallback(async () => {
    try {
      const loaded = await client.get(goalId);
      setGoal(loaded);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, goalId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAction(action: () => Promise<CanonicalGoal>) {
    setBusy(true);
    try {
      const updated = await action();
      setGoal(updated);
      setActionError(null);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  }

  async function submitRevision(event: FormEvent) {
    event.preventDefault();
    if (!goal) return;
    await runAction(() =>
      client.revise(goal.id, {
        expected_revision: goal.revision,
        title: revisionTitle.trim() || undefined,
        objective: revisionObjective.trim() || undefined,
        active_task_policy: activeTaskPolicy,
        reopen_terminal: reopenTerminal,
      }),
    );
    setRevisionTitle("");
    setRevisionObjective("");
    setReopenTerminal(false);
  }

  async function submitAttach(event: FormEvent) {
    event.preventDefault();
    if (!goal) return;
    const canonicalTaskId = requireText(taskId, "task ID");
    await runAction(() => client.attachTask(goal.id, canonicalTaskId, goal.revision));
    setTaskId("");
  }

  async function submitReview(event: FormEvent) {
    event.preventDefault();
    if (!goal) return;
    const evidence = parseEvidence(evidenceJson);
    await runAction(() =>
      client.review(goal.id, {
        expected_revision: goal.revision,
        trigger_ref: requireText(reviewTrigger, "review trigger"),
        evidence,
        next_review_at: nextReviewAt.trim() || undefined,
      }),
    );
  }

  if (error && goal === null) return <ErrorState error={error} onRetry={() => void load()} />;
  if (goal === null) return <LoadingState />;

  const canPause = goal.status === "active" || goal.status === "waiting";
  const canResume = goal.status === "paused";
  const canActivate = goal.status === "draft";
  const canCancel = !TERMINAL_GOAL_STATES.has(goal.status);
  const canFail = goal.status === "active" || goal.status === "waiting";

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Durable Goal</p>
          <h1>{goal.title}</h1>
          <CanonicalId value={goal.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={goal.status} />
          <StatusBadge value={goal.progress} />
          <span>revision {goal.revision}</span>
        </div>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {actionError ? <ErrorState error={actionError} /> : null}

      <Card title="Lifecycle controls">
        <div className="actions">
          {canActivate ? <button disabled={busy} onClick={() => void runAction(() => client.activate(goal.id))}>Activate</button> : null}
          {canPause ? <button disabled={busy} onClick={() => void runAction(() => client.pause(goal.id))}>Pause</button> : null}
          {canResume ? <button disabled={busy} onClick={() => void runAction(() => client.resume(goal.id))}>Resume</button> : null}
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
        </div>
        {canCancel ? (
          <div className="form-grid">
            <label>
              Cancellation reason
              <input value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} />
            </label>
            <div className="actions">
              <button
                disabled={busy || !cancelReason.trim()}
                onClick={() => void runAction(() => client.cancel(goal.id, cancelReason.trim()))}
              >
                Cancel Goal
              </button>
            </div>
          </div>
        ) : null}
        {canFail ? (
          <div className="form-grid">
            <label>
              Failure reason
              <input value={failureReason} onChange={(event) => setFailureReason(event.target.value)} />
            </label>
            <div className="actions">
              <button
                disabled={busy || !failureReason.trim()}
                onClick={() => void runAction(() => client.fail(goal.id, failureReason.trim()))}
              >
                Mark Goal failed
              </button>
            </div>
          </div>
        ) : null}
        <p>
          Human pause, resume, cancellation, reasoned failure and revision go through canonical Goal
          commands. The browser does not mutate Goal state locally or bypass server-side authorization.
        </p>
      </Card>

      <div className="grid-two">
        <Card title="Objective and scope">
          <DefinitionList values={{
            objective: goal.objective,
            owner: `${goal.owner_ref.type}:${goal.owner_ref.id}`,
            project: goal.project_id ?? "—",
            deadline: formatDate(goal.deadline),
            next_review: formatDate(goal.next_review_at),
            terminal_reason: goal.terminal_reason ?? "—",
            digest: goal.digest,
          }} />
        </Card>
        <Card title="Autonomy and observation">
          <DefinitionList values={{
            automation: goal.observation_policy.automation_id ?? "—",
            review_interval_seconds: goal.observation_policy.review_interval_seconds ?? "—",
            event_types: goal.observation_policy.event_types.join(", ") || "—",
            task_generation: goal.task_generation_policy.enabled ? "enabled" : "disabled",
            proposal_required: String(goal.task_generation_policy.proposal_required),
            max_tasks_per_review: goal.autonomy_policy.max_tasks_per_review,
            max_failed_cycles: goal.autonomy_policy.max_consecutive_failed_cycles,
            consecutive_failed_cycles: goal.consecutive_failed_cycles,
            human_checkpoint: String(goal.autonomy_policy.human_checkpoint_required),
          }} />
        </Card>
      </div>

      <Card title="Success criteria">
        <CriteriaTable criteria={goal.success_criteria} latest={goal.reviews.at(-1)?.criterion_evaluations ?? []} />
      </Card>

      <Card title="Constraints">
        <ConstraintGrid constraints={goal.constraints} />
      </Card>

      <Card title="Linked Tasks and provenance">
        <TaskLinks goal={goal} />
        <form className="stack" onSubmit={(event) => void submitAttach(event)}>
          <div className="form-grid">
            <label>
              Existing canonical Task ID
              <input required value={taskId} onChange={(event) => setTaskId(event.target.value)} />
            </label>
          </div>
          <div className="actions">
            <button disabled={busy || TERMINAL_GOAL_STATES.has(goal.status)} type="submit">Attach Task to revision {goal.revision}</button>
            <AppLink href="/tasks">Create a Task in the canonical Task surface</AppLink>
          </div>
        </form>
      </Card>

      <Card title="Review Goal now">
        <p>
          Manual review uses the same explicit evidence contract as scheduled reviews. Browser
          evidence cannot self-promote to verified truth: human acceptance is bound to the
          authenticated user, while other verified evidence must enter through a canonical verifier.
        </p>
        <form className="stack" onSubmit={(event) => void submitReview(event)}>
          <div className="form-grid">
            <label>
              Trigger reference
              <input required value={reviewTrigger} onChange={(event) => setReviewTrigger(event.target.value)} />
            </label>
            <label>
              Next review at (optional ISO-8601)
              <input value={nextReviewAt} onChange={(event) => setNextReviewAt(event.target.value)} />
            </label>
          </div>
          <label>
            Evidence JSON
            <textarea rows={8} value={evidenceJson} onChange={(event) => setEvidenceJson(event.target.value)} />
          </label>
          <div className="actions">
            <button disabled={busy || goal.status === "paused" || TERMINAL_GOAL_STATES.has(goal.status)} type="submit">Review revision {goal.revision}</button>
          </div>
        </form>
      </Card>

      <Card title="Revise Goal">
        <form className="stack" onSubmit={(event) => void submitRevision(event)}>
          <div className="form-grid">
            <label>
              New title (optional)
              <input value={revisionTitle} onChange={(event) => setRevisionTitle(event.target.value)} />
            </label>
            <label>
              Active Task policy
              <select value={activeTaskPolicy} onChange={(event) => setActiveTaskPolicy(event.target.value as GoalTaskRevisionPolicy)}>
                <option value="retain">Retain linked work</option>
                <option value="supersede">Supersede linked work for new revision</option>
              </select>
            </label>
          </div>
          <label>
            New objective (optional)
            <textarea rows={3} value={revisionObjective} onChange={(event) => setRevisionObjective(event.target.value)} />
          </label>
          <label>
            <input type="checkbox" checked={reopenTerminal} onChange={(event) => setReopenTerminal(event.target.checked)} />
            Reopen a satisfied/failed Goal as part of this explicit revision
          </label>
          <div className="actions">
            <button disabled={busy || (!revisionTitle.trim() && !revisionObjective.trim() && !reopenTerminal)} type="submit">
              Create revision {goal.revision + 1}
            </button>
          </div>
        </form>
      </Card>

      <Card title="Evidence history">
        <EvidenceTable goal={goal} />
      </Card>

      <Card title="Review / progress history">
        <ReviewHistory goal={goal} />
      </Card>
    </div>
  );
}

export function GoalTable({ goals }: { goals: CanonicalGoal[] }) {
  if (goals.length === 0) return <EmptyState title="No Goals configured" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Goal</th><th>Status</th><th>Progress</th><th>Revision</th><th>Active Tasks</th><th>Next review</th></tr>
        </thead>
        <tbody>
          {goals.map((goal) => (
            <tr key={goal.id}>
              <td>
                <AppLink href={`/goals/${encodeURIComponent(goal.id)}`}>
                  {goal.title}<br /><CanonicalId value={goal.id} />
                </AppLink>
              </td>
              <td><StatusBadge value={goal.status} /></td>
              <td><StatusBadge value={goal.progress} /></td>
              <td>{goal.revision}</td>
              <td>{goal.active_task_ids.length}</td>
              <td>{formatDate(goal.next_review_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CriteriaTable({
  criteria,
  latest,
}: {
  criteria: CanonicalGoalCriterion[];
  latest: CanonicalGoal["reviews"][number]["criterion_evaluations"];
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Criterion</th><th>Kind</th><th>Rule</th><th>Required</th><th>Latest state</th></tr></thead>
        <tbody>
          {criteria.map((criterion) => {
            const evaluation = latest.find((item) => item.criterion_id === criterion.criterion_id);
            return (
              <tr key={criterion.criterion_id}>
                <td>{criterion.description}<br /><code>{criterion.criterion_id}</code></td>
                <td>{criterion.kind}</td>
                <td>{criterion.operator} {renderJson(criterion.target)}</td>
                <td>{criterion.required ? "yes" : "no"}</td>
                <td>{evaluation ? <StatusBadge value={evaluation.state} /> : "not evaluated"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ConstraintGrid({ constraints }: { constraints: CanonicalGoalConstraints }) {
  return (
    <div className="grid-two">
      <StringList title="Requirements" values={constraints.requirements} />
      <StringList title="Out of scope" values={constraints.out_of_scope} />
      <StringList title="Risk requirements" values={constraints.risk_requirements} />
      <StringList title="Data requirements" values={constraints.data_requirements} />
      <StringList title="Security requirements" values={constraints.security_requirements} />
    </div>
  );
}

function StringList({ title, values }: { title: string; values: string[] }) {
  return (
    <section>
      <h3>{title}</h3>
      {values.length ? <ul>{values.map((value) => <li key={value}>{value}</li>)}</ul> : <p>—</p>}
    </section>
  );
}

function TaskLinks({ goal }: { goal: CanonicalGoal }) {
  if (goal.linked_tasks.length === 0) return <EmptyState title="No Tasks linked yet" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Task</th><th>Task state</th><th>Goal revision</th><th>Review</th><th>Valid now</th></tr></thead>
        <tbody>
          {goal.linked_tasks.map((link) => (
            <tr key={`${link.task_id}:${link.goal_revision}:${link.created_at}`}>
              <td><AppLink href={`/tasks/${encodeURIComponent(link.task_id)}`}><CanonicalId value={link.task_id} /></AppLink></td>
              <td><StatusBadge value={link.task_state} /></td>
              <td>{link.goal_revision}</td>
              <td>{link.review_id ?? "human/direct"}</td>
              <td>{link.valid_for_current_revision ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EvidenceTable({ goal }: { goal: CanonicalGoal }) {
  if (goal.evidence.length === 0) return <EmptyState title="No canonical Goal evidence recorded" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Evidence</th><th>Criterion</th><th>Kind</th><th>Value</th><th>Verified</th><th>Source</th><th>Observed</th></tr></thead>
        <tbody>
          {goal.evidence.map((evidence) => (
            <tr key={evidence.evidence_id}>
              <td><CanonicalId value={evidence.evidence_id} /></td>
              <td>{evidence.criterion_id}</td>
              <td>{evidence.kind}</td>
              <td><code>{renderJson(evidence.value)}</code></td>
              <td>{evidence.verified ? "yes" : "no"}</td>
              <td>{evidence.source_ref}</td>
              <td>{formatDate(evidence.observed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReviewHistory({ goal }: { goal: CanonicalGoal }) {
  if (goal.reviews.length === 0) return <EmptyState title="Goal has not been reviewed yet" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Review</th><th>Revision</th><th>Trigger</th><th>Work required</th><th>Generated Tasks</th><th>Decision</th><th>Reviewed</th></tr></thead>
        <tbody>
          {[...goal.reviews].reverse().map((review) => (
            <tr key={review.review_id}>
              <td><code>{review.review_id}</code></td>
              <td>{review.goal_revision}</td>
              <td>{review.trigger_ref}</td>
              <td>{review.work_required ? "yes" : "no"}</td>
              <td>
                {review.generated_task_ids.length
                  ? review.generated_task_ids.map((id) => (
                      <span key={id}><AppLink href={`/tasks/${encodeURIComponent(id)}`}><CanonicalId value={id} /></AppLink>{" "}</span>
                    ))
                  : "—"}
              </td>
              <td>{review.decision_reason || "—"}</td>
              <td>{formatDate(review.reviewed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return (
    <dl className="definition-list">
      {Object.entries(values).map(([label, value]) => (
        <div key={label}><dt>{label.replaceAll("_", " ")}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><strong>{value}</strong><span>{label}</span></div>;
}

function parseCriteria(raw: string): CanonicalGoalCriterion[] {
  const value = parseJson(raw, "success criteria");
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error("success criteria must be a non-empty JSON array");
  }
  return value as unknown as CanonicalGoalCriterion[];
}

function parseEvidence(raw: string): GoalEvidenceInput[] {
  const value = parseJson(raw, "evidence");
  if (!Array.isArray(value)) throw new Error("evidence must be a JSON array");
  return value as unknown as GoalEvidenceInput[];
}

function parseJson(raw: string, label: string): JsonValue {
  try {
    return JSON.parse(raw) as JsonValue;
  } catch (error) {
    throw new Error(`${label} must contain valid JSON`, { cause: error });
  }
}

function requireText(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} must not be blank`);
  return normalized;
}

function renderJson(value: JsonValue): string {
  return JSON.stringify(value);
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
