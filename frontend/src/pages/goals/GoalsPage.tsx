import { useCallback, useEffect, useState, type FormEvent } from "react";
import { GoalClient, type CanonicalGoal } from "../../api/goals";
import type { Page } from "../../api/types";
import { useCursorPagination } from "../../app/pagination";
import { PaginationControls } from "../../components/Pagination";
import { Card, ErrorState, LoadingState } from "../../components/States";
import { GoalTable } from "./GoalTables";
import {
  DEFAULT_CRITERIA,
  TERMINAL_GOAL_STATES,
  parseCriteria,
  requireText,
} from "./goalInput";

export function GoalInventoryState({
  page,
  error,
  onRetry,
}: {
  page: Page<CanonicalGoal> | null;
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <>
      {error ? <ErrorState error={error} onRetry={onRetry} /> : null}
      {!page && !error ? <LoadingState /> : null}
      {page ? <GoalTable goals={page.items} /> : null}
    </>
  );
}

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
        <GoalInventoryState page={page} error={error} onRetry={() => void load()} />
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

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><strong>{value}</strong><span>{label}</span></div>;
}
