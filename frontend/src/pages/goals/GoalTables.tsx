import type {
  CanonicalGoal,
  CanonicalGoalConstraints,
  CanonicalGoalCriterion,
} from "../../api/goals";
import { AppLink } from "../../app/router";
import { CanonicalId, EmptyState, StatusBadge } from "../../components/States";
import { formatGoalDate, renderJson } from "./goalPresentation";

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
              <td>{formatGoalDate(goal.next_review_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CriteriaTable({
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

export function ConstraintGrid({ constraints }: { constraints: CanonicalGoalConstraints }) {
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

export function TaskLinks({ goal }: { goal: CanonicalGoal }) {
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

export function EvidenceTable({ goal }: { goal: CanonicalGoal }) {
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
              <td>{formatGoalDate(evidence.observed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ReviewHistory({ goal }: { goal: CanonicalGoal }) {
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
              <td>{formatGoalDate(review.reviewed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return (
    <dl className="definition-list">
      {Object.entries(values).map(([label, value]) => (
        <div key={label}><dt>{label.replaceAll("_", " ")}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}
