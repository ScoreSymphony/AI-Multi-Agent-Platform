import type {
  TaskBudgetDimensionProjection,
  TaskExecutionBudgetProjection,
} from "../api/taskBudgets";
import { Card, DegradedState, EmptyState, LoadingState, StatusBadge } from "./States";

export function TaskExecutionBudget({
  budget,
  error,
  loading,
}: {
  budget: TaskExecutionBudgetProjection | null;
  error: unknown;
  loading: boolean;
}) {
  return (
    <Card title="Execution budget">
      {error != null ? (
        <DegradedState
          title="Execution budget unavailable"
          detail={error instanceof Error ? error.message : "The budget projection could not be loaded."}
        />
      ) : loading ? (
        <LoadingState />
      ) : budget === null ? (
        <EmptyState title="No Task execution budget configured" />
      ) : (
        <div className="stack">
          <div className="detail-status">
            <StatusBadge value={budget.blocking_dimensions.length ? "blocked" : budget.warnings.length ? "warning" : "within budget"} />
            <span>Policy v{budget.policy_version}</span>
            <small>Observed {formatDate(budget.observed_at)}</small>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Dimension</th>
                  <th>Limit</th>
                  <th>Consumed</th>
                  <th>Reserved</th>
                  <th>Remaining</th>
                  <th>Measurement</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {budget.dimensions.map((dimension) => (
                  <BudgetDimensionRow key={dimension.dimension} dimension={dimension} />
                ))}
              </tbody>
            </table>
          </div>
          <dl>
            <div><dt>Runtime origin</dt><dd>{formatDate(budget.started_at)}</dd></div>
            <div><dt>Last policy update</dt><dd>{formatDate(budget.updated_at)}</dd></div>
            <div><dt>Policy revisions</dt><dd>{budget.history.length}</dd></div>
            <div>
              <dt>Trace refs</dt>
              <dd>{budget.trace_refs.length ? budget.trace_refs.join(", ") : "—"}</dd>
            </div>
          </dl>
        </div>
      )}
    </Card>
  );
}

function BudgetDimensionRow({ dimension }: { dimension: TaskBudgetDimensionProjection }) {
  const state = dimension.exhausted
    ? "exhausted"
    : dimension.overrun
      ? "overrun"
      : dimension.warning
        ? "warning"
        : "available";
  const quality = Object.entries(dimension.quality_counts)
    .filter(([, count]) => (count ?? 0) > 0)
    .map(([name, count]) => `${name}:${count}`);
  if (dimension.unavailable_count > 0) quality.push(`unavailable:${dimension.unavailable_count}`);

  return (
    <tr>
      <td>{dimension.dimension}</td>
      <td>{formatQuantity(dimension.limit, dimension.unit)}</td>
      <td>{formatQuantity(dimension.consumed, dimension.unit)}</td>
      <td>{formatQuantity(dimension.reserved, dimension.unit)}</td>
      <td>{formatQuantity(dimension.remaining, dimension.unit)}</td>
      <td>{quality.length ? quality.join(", ") : dimension.source}</td>
      <td><StatusBadge value={state} /></td>
    </tr>
  );
}

function formatQuantity(value: number, unit: string | null): string {
  const rendered = Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return unit ? `${rendered} ${unit}` : rendered;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
