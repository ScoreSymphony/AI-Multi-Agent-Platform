import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  EvaluationClient,
  type CanonicalEvaluationComparison,
  type CanonicalEvaluationRun,
  type CanonicalEvaluationSuite,
  type EvaluationConfigurationSnapshot,
  type EvaluationSnapshotValue,
  type EvaluationVersionReference,
} from "../api/evaluations";
import type { Page } from "../api/types";
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

const SUITE_QUERY_KEY = "evaluation-suites:id:asc";
const RUN_QUERY_KEY = "evaluation-runs:started_at:desc";

export function EvaluationsPage({ client }: { client: EvaluationClient }) {
  const [suites, setSuites] = useState<Page<CanonicalEvaluationSuite> | null>(null);
  const [runs, setRuns] = useState<Page<CanonicalEvaluationRun> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const suitePagination = useCursorPagination(SUITE_QUERY_KEY);
  const runPagination = useCursorPagination(RUN_QUERY_KEY);

  const load = useCallback(async () => {
    try {
      const [nextSuites, nextRuns] = await Promise.all([
        client.listSuites({
          limit: 50,
          cursor: suitePagination.cursor,
          sort: "id",
          direction: "asc",
        }),
        client.listRuns({
          limit: 50,
          cursor: runPagination.cursor,
          sort: "started_at",
          direction: "desc",
        }),
      ]);
      setSuites(nextSuites);
      setRuns(nextRuns);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, runPagination.cursor, suitePagination.cursor]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Quality and regression evidence</p>
        <h1>Evaluations</h1>
        <p>
          Versioned suites and durable evaluation runs from the canonical Control Plane. Results
          remain linked to the exact configuration snapshot, evaluator and underlying Task/Run
          evidence that produced them.
        </p>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      <Card title="Evaluation suites">
        {!suites && !error ? <LoadingState /> : null}
        {suites ? <EvaluationSuiteTable suites={suites.items} /> : null}
        {suites ? (
          <PaginationControls
            page={suites}
            pageNumber={suitePagination.pageNumber}
            hasPrevious={suitePagination.hasPrevious}
            onPrevious={suitePagination.previous}
            onRefresh={() => void load()}
            onNext={() => suitePagination.next(suites.next_cursor)}
          />
        ) : null}
      </Card>

      <Card title="Evaluation runs">
        {!runs && !error ? <LoadingState /> : null}
        {runs ? <EvaluationRunTable runs={runs.items} /> : null}
        {runs ? (
          <PaginationControls
            page={runs}
            pageNumber={runPagination.pageNumber}
            hasPrevious={runPagination.hasPrevious}
            onPrevious={runPagination.previous}
            onRefresh={() => void load()}
            onNext={() => runPagination.next(runs.next_cursor)}
          />
        ) : null}
      </Card>
    </div>
  );
}

export function EvaluationSuiteDetailPage({
  client,
  suiteRef,
}: {
  client: EvaluationClient;
  suiteRef: string;
}) {
  const [suite, setSuite] = useState<CanonicalEvaluationSuite | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);
  const [createdRun, setCreatedRun] = useState<CanonicalEvaluationRun | null>(null);

  const load = useCallback(async () => {
    try {
      setSuite(await client.getSuite(suiteRef));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, suiteRef]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runEvaluation(draft: RunDraft) {
    setRunning(true);
    try {
      const repetitions = parsePositiveInteger(draft.repetitions, "Repetitions");
      const baselineRunId = emptyToNull(draft.baselineRunId);
      const regressionPolicyRef = emptyToNull(draft.regressionPolicyRef);
      const result = await client.runSuite(suiteRef, {
        snapshot: {
          platform_version: draft.platformVersion.trim(),
          platform_commit: emptyToNull(draft.platformCommit),
          references: parseVersionReferences(draft.referencesJson),
          environment: parseEnvironmentValues(draft.environmentJson),
        },
        repetitions,
        seed: parseOptionalInteger(draft.seed, "Seed"),
        baseline_run_id: baselineRunId,
        regression_policy_ref: regressionPolicyRef,
      });
      setCreatedRun(result);
      setActionError(null);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setRunning(false);
    }
  }

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!suite) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Evaluation suite</p>
        <h1>{suite.name}</h1>
        <p>{suite.description || "Versioned canonical evaluation suite."}</p>
        <p><CanonicalId value={suite.id} /> · version {suite.version}</p>
      </header>

      <Card title="Cases">
        <EvaluationCaseTable suite={suite} />
      </Card>

      <Card title="Run this suite">
        <p>
          The immutable configuration snapshot is sent to the canonical `evaluation.run` command.
          Record the exact version references and non-secret environment metadata required to make
          later comparisons meaningful. The browser does not construct evaluator, model-provider
          or execution lifecycle state.
        </p>
        {actionError ? <ErrorState error={actionError} /> : null}
        <RunEvaluationForm disabled={running} onSubmit={runEvaluation} />
        {createdRun ? (
          <p>
            Created run{" "}
            <AppLink href={`/evaluations/runs/${encodeURIComponent(createdRun.id)}`}>
              <CanonicalId value={createdRun.id} />
            </AppLink>
          </p>
        ) : null}
      </Card>
    </div>
  );
}

export function EvaluationRunDetailPage({
  client,
  evaluationRunId,
}: {
  client: EvaluationClient;
  evaluationRunId: string;
}) {
  const [run, setRun] = useState<CanonicalEvaluationRun | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [comparing, setComparing] = useState(false);
  const [comparison, setComparison] = useState<CanonicalEvaluationComparison | null>(null);

  const load = useCallback(async () => {
    try {
      const loaded = await client.getRun(evaluationRunId);
      setRun(loaded);
      setComparison(loaded.comparison ?? null);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, evaluationRunId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function compare(draft: CompareDraft) {
    setComparing(true);
    try {
      const result = await client.compareRuns(
        evaluationRunId,
        draft.baselineRunId.trim(),
        draft.regressionPolicyRef.trim(),
      );
      setComparison(result);
      setActionError(null);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setComparing(false);
    }
  }

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!run) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Evaluation run</p>
        <h1><CanonicalId value={run.id} /></h1>
        <p>
          <StatusBadge value={run.status} /> · suite {run.suite_id}@{run.suite_version}
        </p>
      </header>

      <Card title="Configuration snapshot">
        <SnapshotSummary snapshot={run.snapshot} />
      </Card>

      <Card title="Results">
        <EvaluationResultTable run={run} />
      </Card>

      <Card title="Regression comparison">
        {comparison ? <ComparisonSummary comparison={comparison} /> : (
          <p>No persisted comparison is attached to this run.</p>
        )}
        {actionError ? <ErrorState error={actionError} /> : null}
        <CompareForm disabled={comparing} onSubmit={compare} />
      </Card>
    </div>
  );
}

export function EvaluationSuiteTable({ suites }: { suites: CanonicalEvaluationSuite[] }) {
  if (suites.length === 0) return <EmptyState title="No evaluation suites" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Suite</th><th>Version</th><th>Cases</th><th>Tags</th></tr></thead>
        <tbody>
          {suites.map((suite) => (
            <tr key={suite.id}>
              <td>
                <AppLink href={`/evaluations/suites/${encodeURIComponent(suite.id)}`}>
                  {suite.name}
                </AppLink>
                <div><CanonicalId value={suite.id} /></div>
              </td>
              <td>{suite.version}</td>
              <td>{suite.cases.length}</td>
              <td>{suite.tags.join(", ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EvaluationRunTable({ runs }: { runs: CanonicalEvaluationRun[] }) {
  if (runs.length === 0) return <EmptyState title="No evaluation runs" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Run</th><th>Status</th><th>Suite</th><th>Platform</th><th>Baseline</th></tr></thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>
                <AppLink href={`/evaluations/runs/${encodeURIComponent(run.id)}`}>
                  <CanonicalId value={run.id} />
                </AppLink>
              </td>
              <td><StatusBadge value={run.status} /></td>
              <td>{run.suite_id}@{run.suite_version}</td>
              <td>{run.snapshot.platform_version}</td>
              <td>{run.baseline_run_id ? <CanonicalId value={run.baseline_run_id} /> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EvaluationCaseTable({ suite }: { suite: CanonicalEvaluationSuite }) {
  if (suite.cases.length === 0) return <EmptyState title="No cases in this suite" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Case</th><th>Category</th><th>Checks</th><th>Fixtures</th><th>Tags</th></tr>
        </thead>
        <tbody>
          {suite.cases.map((item) => (
            <tr key={`${item.case_id}@${item.version}`}>
              <td>{item.name}<div><CanonicalId value={`${item.case_id}@${item.version}`} /></div></td>
              <td>{item.category || "—"}</td>
              <td>
                {item.assertion_count} assertions · {item.metric_rule_count} metrics ·{" "}
                {item.rubric_criterion_count} rubric
              </td>
              <td>{item.fixtures.join(", ") || "—"}</td>
              <td>{item.tags.join(", ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EvaluationResultTable({ run }: { run: CanonicalEvaluationRun }) {
  const results = run.results ?? [];
  if (results.length === 0) return <EmptyState title="No evaluation results" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Case</th><th>Outcome</th><th>Evaluator</th><th>Score</th><th>Evidence</th></tr>
        </thead>
        <tbody>
          {results.map((result) => (
            <tr key={result.id}>
              <td>{result.case_id}@{result.case_version}</td>
              <td><StatusBadge value={result.outcome} /></td>
              <td>
                {result.evaluator.evaluator_id}@{result.evaluator.version}
                <div>{result.evaluator.deterministic ? "deterministic" : result.evaluator.kind}</div>
              </td>
              <td>{result.score ?? (result.deterministic_pass === null ? "—" : String(result.deterministic_pass))}</td>
              <td>
                {result.task_id ? <AppLink href={`/tasks/${encodeURIComponent(result.task_id)}`}>Task</AppLink> : null}
                {result.task_id && result.run_id ? " · " : null}
                {result.run_id ? <AppLink href={`/runs/${encodeURIComponent(result.run_id)}`}>Run</AppLink> : null}
                {!result.task_id && !result.run_id ? "—" : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SnapshotSummary({ snapshot }: { snapshot: EvaluationConfigurationSnapshot }) {
  return (
    <dl>
      <dt>Platform version</dt><dd>{snapshot.platform_version}</dd>
      <dt>Platform commit</dt><dd>{snapshot.platform_commit || "—"}</dd>
      {snapshot.snapshot_id ? <><dt>Snapshot</dt><dd><CanonicalId value={snapshot.snapshot_id} /></dd></> : null}
      <dt>Version references</dt>
      <dd>
        {snapshot.references.length === 0 ? "—" : snapshot.references.map((item) => (
          <div key={`${item.kind}:${item.ref_id}:${item.version}:${item.revision ?? ""}`}>
            {item.kind}: {item.ref_id}@{item.version}{item.revision ? ` (${item.revision})` : ""}
          </div>
        ))}
      </dd>
      <dt>Environment metadata</dt>
      <dd>
        {snapshot.environment.length === 0 ? "—" : snapshot.environment.map((item) => (
          <div key={item.key}>{item.key}={item.value}</div>
        ))}
      </dd>
    </dl>
  );
}

function ComparisonSummary({ comparison }: { comparison: CanonicalEvaluationComparison }) {
  return (
    <div className="stack">
      <p>
        Baseline <CanonicalId value={comparison.baseline_run_id} /> · policy{" "}
        {comparison.policy_id}@{comparison.policy_version}
      </p>
      <p>{comparison.regression_count} regressions · {comparison.improvement_count} improvements</p>
      {comparison.findings.length === 0 ? <EmptyState title="No comparison findings" /> : (
        <ul>
          {comparison.findings.map((finding) => (
            <li key={`${finding.kind}:${finding.rule_id}:${finding.case_id}:${finding.current_result_id}`}>
              <strong>{finding.kind}</strong> · {finding.case_id} · {finding.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface RunDraft {
  platformVersion: string;
  platformCommit: string;
  referencesJson: string;
  environmentJson: string;
  repetitions: string;
  seed: string;
  baselineRunId: string;
  regressionPolicyRef: string;
}

function RunEvaluationForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (draft: RunDraft) => void | Promise<void>;
}) {
  const [draft, setDraft] = useState<RunDraft>({
    platformVersion: "",
    platformCommit: "",
    referencesJson: "[]",
    environmentJson: "[]",
    repetitions: "1",
    seed: "",
    baselineRunId: "",
    regressionPolicyRef: "",
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void onSubmit(draft);
  }

  return (
    <form className="stack" onSubmit={submit}>
      <label>
        Platform version
        <input
          required
          value={draft.platformVersion}
          onChange={(event) => setDraft({ ...draft, platformVersion: event.target.value })}
        />
      </label>
      <label>
        Platform commit (optional)
        <input
          value={draft.platformCommit}
          onChange={(event) => setDraft({ ...draft, platformCommit: event.target.value })}
        />
      </label>
      <label>
        Version references JSON
        <textarea
          rows={6}
          spellCheck={false}
          value={draft.referencesJson}
          onChange={(event) => setDraft({ ...draft, referencesJson: event.target.value })}
        />
      </label>
      <small>
        Array of objects with `kind`, `ref_id`, `version` and optional `revision`.
      </small>
      <label>
        Environment metadata JSON
        <textarea
          rows={4}
          spellCheck={false}
          value={draft.environmentJson}
          onChange={(event) => setDraft({ ...draft, environmentJson: event.target.value })}
        />
      </label>
      <small>Array of non-secret `key` / `value` metadata records.</small>
      <label>
        Repetitions
        <input
          inputMode="numeric"
          value={draft.repetitions}
          onChange={(event) => setDraft({ ...draft, repetitions: event.target.value })}
        />
      </label>
      <label>
        Seed (optional)
        <input
          inputMode="numeric"
          value={draft.seed}
          onChange={(event) => setDraft({ ...draft, seed: event.target.value })}
        />
      </label>
      <label>
        Baseline run ID (optional)
        <input
          value={draft.baselineRunId}
          onChange={(event) => setDraft({ ...draft, baselineRunId: event.target.value })}
        />
      </label>
      <label>
        Regression policy ref (required with a baseline)
        <input
          placeholder="policy-id@version"
          value={draft.regressionPolicyRef}
          onChange={(event) => setDraft({ ...draft, regressionPolicyRef: event.target.value })}
        />
      </label>
      <div className="actions">
        <button disabled={disabled} type="submit">{disabled ? "Running…" : "Run evaluation"}</button>
      </div>
    </form>
  );
}

interface CompareDraft {
  baselineRunId: string;
  regressionPolicyRef: string;
}

function CompareForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (draft: CompareDraft) => void | Promise<void>;
}) {
  const [draft, setDraft] = useState<CompareDraft>({ baselineRunId: "", regressionPolicyRef: "" });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void onSubmit(draft);
  }
  return (
    <form className="stack" onSubmit={submit}>
      <label>
        Baseline run ID
        <input
          required
          value={draft.baselineRunId}
          onChange={(event) => setDraft({ ...draft, baselineRunId: event.target.value })}
        />
      </label>
      <label>
        Regression policy ref
        <input
          required
          placeholder="policy-id@version"
          value={draft.regressionPolicyRef}
          onChange={(event) => setDraft({ ...draft, regressionPolicyRef: event.target.value })}
        />
      </label>
      <div className="actions">
        <button disabled={disabled} type="submit">{disabled ? "Comparing…" : "Compare with baseline"}</button>
      </div>
    </form>
  );
}

function parseVersionReferences(value: string): EvaluationVersionReference[] {
  return parseJsonArray(value, "Version references").map((entry, index) => {
    const record = parseObject(entry, `Version references[${index}]`);
    const revision = optionalString(record.revision, `Version references[${index}].revision`);
    return {
      kind: requiredString(record.kind, `Version references[${index}].kind`),
      ref_id: requiredString(record.ref_id, `Version references[${index}].ref_id`),
      version: requiredString(record.version, `Version references[${index}].version`),
      revision,
    };
  });
}

function parseEnvironmentValues(value: string): EvaluationSnapshotValue[] {
  return parseJsonArray(value, "Environment metadata").map((entry, index) => {
    const record = parseObject(entry, `Environment metadata[${index}]`);
    return {
      key: requiredString(record.key, `Environment metadata[${index}].key`),
      value: requiredString(record.value, `Environment metadata[${index}].value`),
    };
  });
}

function parseJsonArray(value: string, label: string): unknown[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value) as unknown;
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
  if (!Array.isArray(parsed)) throw new Error(`${label} must be a JSON array`);
  return parsed;
}

function parseObject(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${label} must be a non-blank string`);
  return value;
}

function optionalString(value: unknown, label: string): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${label} must be a non-blank string or null`);
  }
  return value;
}

function emptyToNull(value: string): string | null {
  const normalized = value.trim();
  return normalized ? normalized : null;
}

function parsePositiveInteger(value: string, label: string): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) throw new Error(`${label} must be a positive integer`);
  return parsed;
}

function parseOptionalInteger(value: string, label: string): number | null {
  const normalized = value.trim();
  if (!normalized) return null;
  const parsed = Number(normalized);
  if (!Number.isInteger(parsed)) throw new Error(`${label} must be an integer`);
  return parsed;
}
