import type {
  PlanCoordinationProjection,
  PlanCoordinationStep,
} from "../api/workflowProgress";
import { AppLink } from "../app/router";
import { CanonicalId, EmptyState, StatusBadge } from "./States";

export function WorkflowProgress({
  projection,
}: {
  projection: PlanCoordinationProjection;
}) {
  const waiting = projection.steps.filter(
    (step) => step.wait_type !== null || step.wait_deadline_at !== null,
  ).length;
  const retrying = projection.steps.filter(
    (step) => step.retry_due_at !== null || step.current_attempt > 1,
  ).length;

  if (projection.steps.length === 0) {
    return (
      <div className="stack">
        <WorkflowSummary projection={projection} waiting={0} retrying={0} />
        <EmptyState title="No workflow steps" />
      </div>
    );
  }

  return (
    <div className="stack">
      <WorkflowSummary projection={projection} waiting={waiting} retrying={retrying} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Step</th>
              <th>Status</th>
              <th>Dependencies</th>
              <th>Latest Run</th>
              <th>Attempt</th>
              <th>Wait</th>
              <th>Retry</th>
              <th>Reconciliation</th>
            </tr>
          </thead>
          <tbody>
            {projection.steps.map((step) => (
              <WorkflowStepRow key={step.id} step={step} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function WorkflowSummary({
  projection,
  waiting,
  retrying,
}: {
  projection: PlanCoordinationProjection;
  waiting: number;
  retrying: number;
}) {
  return (
    <dl className="definition-list">
      <dt>Plan</dt><dd><CanonicalId value={projection.id} /></dd>
      <dt>Revision</dt><dd>{projection.plan_revision}</dd>
      <dt>Steps</dt><dd>{projection.steps.length}</dd>
      <dt>Waiting</dt><dd>{waiting}</dd>
      <dt>Retrying / retried</dt><dd>{retrying}</dd>
    </dl>
  );
}

function WorkflowStepRow({ step }: { step: PlanCoordinationStep }) {
  const satisfied = step.satisfied_dependency_ids.length;
  const totalDependencies = step.dependency_ids.length;
  const wait = step.wait_type ?? (step.wait_deadline_at ? "deadline" : null);

  return (
    <tr>
      <td>
        <CanonicalId value={step.id} />
        <div><small>{step.coordination_phase}</small></div>
      </td>
      <td><StatusBadge value={step.status} /></td>
      <td>
        {totalDependencies === 0 ? (
          "—"
        ) : (
          <>
            <div>{satisfied}/{totalDependencies} satisfied</div>
            <ul>
              {step.dependency_ids.map((dependencyId) => (
                <li key={dependencyId}>
                  <CanonicalId value={dependencyId} />
                  {step.satisfied_dependency_ids.includes(dependencyId) ? " · satisfied" : ""}
                </li>
              ))}
            </ul>
          </>
        )}
      </td>
      <td>
        {step.latest_run_id ? (
          <AppLink href={`/runs/${step.latest_run_id}`}>
            <CanonicalId value={step.latest_run_id} />
          </AppLink>
        ) : "—"}
      </td>
      <td>{step.current_attempt}</td>
      <td>
        {wait ?? "—"}
        {step.wait_deadline_at ? <div><small>until {formatDate(step.wait_deadline_at)}</small></div> : null}
      </td>
      <td>
        {step.retry_due_at ? formatDate(step.retry_due_at) : step.current_attempt > 1 ? "previous retry" : "—"}
      </td>
      <td>
        <StatusBadge value={step.reconciliation} />
        {step.reconciliation_detail ? <div><small>{step.reconciliation_detail}</small></div> : null}
      </td>
    </tr>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
