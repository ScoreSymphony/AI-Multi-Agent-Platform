import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  GoalClient,
  type CanonicalGoal,
  type GoalTaskRevisionPolicy,
} from "../../api/goals";
import { AppLink } from "../../app/router";
import {
  Card,
  CanonicalId,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../../components/States";
import {
  ConstraintGrid,
  CriteriaTable,
  DefinitionList,
  EvidenceTable,
  ReviewHistory,
  TaskLinks,
} from "./GoalTables";
import {
  TERMINAL_GOAL_STATES,
  parseEvidence,
  requireText,
} from "./goalInput";
import { formatGoalDate } from "./goalPresentation";

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
            deadline: formatGoalDate(goal.deadline),
            next_review: formatGoalDate(goal.next_review_at),
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
