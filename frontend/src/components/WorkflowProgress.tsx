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
  const waiting = projection.steps.filter((step) => step.wait_state === "active").length;
  const retrying = projection.steps.filter(
    (step) => step.retry_state !== null && step.retry_state !== "none",
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
      <dt>Retry state</dt><dd>{retrying}</dd>
    </dl>
  );
}

function WorkflowStepRow({ step }: { step: PlanCoordinationStep }) {
  const satisfied = step.satisfied_dependency_ids.length;
  const totalDependencies = step.dependency_ids.length;

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
      <td>
        {step.current_attempt}
        {step.retry_max_attempts !== null ? <div><small>max {step.retry_max_attempts}</small></div> : null}
      </td>
      <td><WaitDetails step={step} /></td>
      <td><RetryDetails step={step} /></td>
      <td>
        <StatusBadge value={step.reconciliation} />
        {step.reconciliation_detail ? <div><small>{step.reconciliation_detail}</small></div> : null}
      </td>
    </tr>
  );
}

function WaitDetails({ step }: { step: PlanCoordinationStep }) {
  if (step.wait_state === null || step.wait_type === null) return <>—</>;
  return (
    <div>
      <StatusBadge value={step.wait_state} />
      <div><small>{step.wait_type}</small></div>
      {step.wait_key ? <div><small>wait {step.wait_key}</small></div> : null}
      {step.wait_deadline_at ? <div><small>deadline {formatDate(step.wait_deadline_at)}</small></div> : null}
      {step.wait_resolved_at ? <div><small>resolved {formatDate(step.wait_resolved_at)}</small></div> : null}
      {step.wait_approval_id ? (
        <div>
          <small>
            approval <CanonicalId value={step.wait_approval_id} />
            {step.wait_approval_action ? ` · ${step.wait_approval_action}` : ""}
          </small>
        </div>
      ) : null}
      {step.wait_approval_subject_type && step.wait_approval_subject_id ? (
        <div><small>subject {step.wait_approval_subject_type}:{step.wait_approval_subject_id}</small></div>
      ) : null}
      {step.wait_event_type ? (
        <div>
          <small>
            event {step.wait_event_type}
            {step.wait_correlation_key ? ` · ${step.wait_correlation_key}` : ""}
          </small>
        </div>
      ) : null}
      {step.wait_external_job_ref ? (
        <div><small>external job {step.wait_external_job_ref}</small></div>
      ) : null}
    </div>
  );
}

function RetryDetails({ step }: { step: PlanCoordinationStep }) {
  if (step.retry_state === null || step.retry_state === "none") return <>—</>;
  return (
    <div>
      <StatusBadge value={step.retry_state} />
      {step.retry_due_at ? <div><small>due {formatDate(step.retry_due_at)}</small></div> : null}
      {step.retry_max_attempts !== null ? (
        <div><small>attempt {step.current_attempt}/{step.retry_max_attempts}</small></div>
      ) : null}
    </div>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
