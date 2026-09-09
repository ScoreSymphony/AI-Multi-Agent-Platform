import type {
  EvaluationManifestComparisonProjection,
  EvaluationManifestProjection,
} from "../api/evaluations";
import { CanonicalId, EmptyState, StatusBadge } from "./States";

export function EvaluationManifestSummary({
  manifest,
}: {
  manifest: EvaluationManifestProjection;
}) {
  return (
    <div className="stack">
      <dl>
        <dt>Manifest</dt>
        <dd><CanonicalId value={manifest.manifest_id} /></dd>
        <dt>Schema</dt><dd>{manifest.schema_version}</dd>
        <dt>Digest</dt><dd><CanonicalId value={manifest.manifest_digest} /></dd>
        <dt>Repeat strategy</dt>
        <dd>
          {manifest.repeat_policy.strategy} · planned {manifest.repeat_policy.repeat_count} · actual{" "}
          {manifest.actual_repeat_count} · {manifest.repeat_completion}
        </dd>
        <dt>Randomness</dt><dd>{manifest.seed_policy.mode}</dd>
        <dt>Environment fingerprint</dt>
        <dd><CanonicalId value={manifest.environment.digest} /></dd>
        <dt>Environment comparability</dt>
        <dd>{manifest.environment.comparability ?? "not compared"}</dd>
      </dl>

      {manifest.seed_policy.ordered_seeds.length > 0 ? (
        <p>Ordered seeds: {manifest.seed_policy.ordered_seeds.join(", ")}</p>
      ) : null}

      {manifest.reproducibility_limitations.length > 0 ? (
        <div>
          <strong>Reproducibility limitations</strong>
          <ul>
            {manifest.reproducibility_limitations.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      <div>
        <strong>Repeat statistics</strong>
        {manifest.repeat_statistics.length === 0 ? (
          <EmptyState title="No repeat statistics" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Case / evaluator</th>
                  <th>Samples</th>
                  <th>Pass rate</th>
                  <th>Mean score</th>
                  <th>Variance</th>
                </tr>
              </thead>
              <tbody>
                {manifest.repeat_statistics.map((item) => (
                  <tr key={`${item.case_id}:${item.case_version}:${item.evaluator_id}`}>
                    <td>{item.case_id}@{item.case_version} · {item.evaluator_id}</td>
                    <td>{item.sample_count}</td>
                    <td>{formatNumber(item.pass_rate)}</td>
                    <td>{formatOptionalNumber(item.score_mean)}</td>
                    <td>{formatOptionalNumber(item.score_variance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div>
        <strong>Per-repeat evidence</strong>
        {manifest.per_repeat_outcomes.length === 0 ? (
          <EmptyState title="No repeat outcomes" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Repeat</th><th>Seed</th><th>Outcomes</th><th>Scores</th></tr>
              </thead>
              <tbody>
                {manifest.per_repeat_outcomes.map((item) => (
                  <tr key={item.repetition_index}>
                    <td>{item.repetition_index}</td>
                    <td>{item.seed ?? "—"}</td>
                    <td>{item.outcomes.join(", ")}</td>
                    <td>{item.scores.map(formatOptionalNumber).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

export function EvaluationManifestComparisonSummary({
  comparison,
}: {
  comparison: EvaluationManifestComparisonProjection;
}) {
  return (
    <div className="stack">
      <p>Manifest comparability: <StatusBadge value={comparison.status} /></p>
      {comparison.differences.length === 0 ? (
        <EmptyState title="No manifest differences" />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Field</th><th>Classification</th><th>Baseline</th><th>Candidate</th></tr>
            </thead>
            <tbody>
              {comparison.differences.map((item) => (
                <tr key={item.path}>
                  <td>{item.path}</td>
                  <td>
                    {item.intentional_candidate_dimension
                      ? "intentional candidate"
                      : item.blocking
                        ? "blocking"
                        : "warning"}
                  </td>
                  <td><code>{formatJson(item.baseline)}</code></td>
                  <td><code>{formatJson(item.candidate)}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function formatOptionalNumber(value: number | null): string {
  return value === null ? "—" : formatNumber(value);
}

function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
}

function formatJson(value: unknown): string {
  return value === undefined ? "—" : JSON.stringify(value);
}
