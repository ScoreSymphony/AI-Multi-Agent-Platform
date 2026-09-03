import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ControlPlaneClient } from "../api/client";
import type {
  APImanifest,
  CanonicalUsageAggregate,
  CanonicalUsageBudget,
  CanonicalUsageRecord,
  MeasurementQuality,
  Page,
} from "../api/types";
import {
  Card,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const accountingCollections = ["usage-records", "usage-aggregates", "usage-budgets"] as const;

type AccountingCollection = (typeof accountingCollections)[number];

export function UsagePage({
  client,
  manifest,
}: {
  client: ControlPlaneClient;
  manifest: APImanifest | null;
}) {
  const [runtimeManifest, setRuntimeManifest] = useState<APImanifest | null>(manifest);
  const [manifestError, setManifestError] = useState<unknown>(null);
  const [records, setRecords] = useState<Page<CanonicalUsageRecord> | null>(null);
  const [aggregates, setAggregates] = useState<Page<CanonicalUsageAggregate> | null>(null);
  const [budgets, setBudgets] = useState<Page<CanonicalUsageBudget> | null>(null);
  const [failures, setFailures] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [scopeDraft, setScopeDraft] = useState("");
  const [scopeFilter, setScopeFilter] = useState("");

  useEffect(() => {
    if (manifest) {
      setRuntimeManifest(manifest);
      setManifestError(null);
      return;
    }
    let active = true;
    void client
      .manifest()
      .then((value) => {
        if (active) setRuntimeManifest(value);
      })
      .catch((error: unknown) => {
        if (active) setManifestError(error);
      });
    return () => {
      active = false;
    };
  }, [client, manifest]);

  const load = useCallback(async () => {
    if (!runtimeManifest) return;
    const available = new Set(runtimeManifest.resources);
    setLoading(true);
    setFailures([]);

    if (!available.has("usage-records")) setRecords(null);
    if (!available.has("usage-aggregates")) setAggregates(null);
    if (!available.has("usage-budgets")) setBudgets(null);

    const requests: Array<{
      collection: AccountingCollection;
      request: Promise<unknown>;
    }> = [];
    if (available.has("usage-records")) {
      requests.push({
        collection: "usage-records",
        request: client.listUsageRecords({
          limit: 100,
          sort: "timestamp",
          direction: "desc",
          q: scopeFilter || undefined,
        }),
      });
    }
    if (available.has("usage-aggregates")) {
      requests.push({
        collection: "usage-aggregates",
        request: client.listUsageAggregates({ limit: 200, sort: "metric_type", direction: "asc" }),
      });
    }
    if (available.has("usage-budgets")) {
      requests.push({
        collection: "usage-budgets",
        request: client.listUsageBudgets({ limit: 200, sort: "metric_type", direction: "asc" }),
      });
    }

    const settled = await Promise.allSettled(requests.map((item) => item.request));
    const nextFailures: string[] = [];
    settled.forEach((result, index) => {
      const collection = requests[index]?.collection;
      if (!collection) return;
      if (result.status === "rejected") {
        nextFailures.push(collection);
        return;
      }
      if (collection === "usage-records") {
        setRecords(result.value as Page<CanonicalUsageRecord>);
      } else if (collection === "usage-aggregates") {
        setAggregates(result.value as Page<CanonicalUsageAggregate>);
      } else {
        setBudgets(result.value as Page<CanonicalUsageBudget>);
      }
    });
    setFailures(nextFailures);
    setLoading(false);
  }, [client, runtimeManifest, scopeFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const applyScopeFilter = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setScopeFilter(scopeDraft.trim());
  };

  if (manifestError && !runtimeManifest) return <ErrorState error={manifestError} />;
  if (!runtimeManifest) return <LoadingState />;

  const missing = accountingCollections.filter(
    (collection) => !runtimeManifest.resources.includes(collection),
  );
  const activeThresholds = budgets?.items.filter((budget) => budget.threshold_level !== null).length ?? 0;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical accounting</p>
        <h1>Usage & limits</h1>
        <p>
          Attributable usage, metric/unit aggregates and configured budget state from registered
          Control Plane accounting resources.
        </p>
      </header>

      {missing.length === accountingCollections.length ? (
        <DegradedState
          title="Accounting extension unavailable"
          detail="The running Control Plane has not registered the #76 usage-records, usage-aggregates or usage-budgets collections. No accounting-store fallback is used."
        />
      ) : null}
      {missing.length > 0 && missing.length < accountingCollections.length ? (
        <DegradedState
          title="Partial accounting surface"
          detail={`Not registered by this Control Plane: ${missing.join(", ")}. Available accounting collections remain usable.`}
        />
      ) : null}
      {failures.length > 0 ? (
        <DegradedState
          title="Partial accounting request failure"
          detail={`Failed Control Plane collections: ${failures.join(", ")}.`}
        />
      ) : null}

      <div className="metrics">
        <Metric label="Visible records" value={records?.total ?? "—"} />
        <Metric label="Metric/unit aggregates" value={aggregates?.total ?? "—"} />
        <Metric label="Budgets" value={budgets?.total ?? "—"} />
        <Metric label="Active thresholds" value={budgets ? activeThresholds : "—"} />
      </div>

      {runtimeManifest.resources.includes("usage-records") ? (
        <Card title="Usage records">
          <form className="toolbar" onSubmit={applyScopeFilter}>
            <label>
              Scope / canonical ID
              <input
                value={scopeDraft}
                onChange={(event) => setScopeDraft(event.target.value)}
                placeholder="task_… / run_… / model ID / node_…"
              />
            </label>
            <button type="submit">Apply filter</button>
            {scopeFilter ? (
              <button
                type="button"
                onClick={() => {
                  setScopeDraft("");
                  setScopeFilter("");
                }}
              >
                Clear
              </button>
            ) : null}
          </form>
          <p className="muted">
            The filter uses the canonical collection search surface, so matching happens before
            pagination. `unavailable` is displayed as unavailable, never as zero.
          </p>
          {loading && !records ? <LoadingState /> : <UsageRecordTable records={records?.items ?? []} />}
        </Card>
      ) : null}

      {runtimeManifest.resources.includes("usage-aggregates") ? (
        <Card title="Aggregates">
          <p className="muted">
            Values remain separated by metric and unit. Additive metrics are summed; latest-mode
            metrics show current point-in-time state. The UI does not combine unlike units or currencies.
          </p>
          {loading && !aggregates ? (
            <LoadingState />
          ) : (
            <UsageAggregateTable aggregates={aggregates?.items ?? []} />
          )}
        </Card>
      ) : null}

      {runtimeManifest.resources.includes("usage-budgets") ? (
        <Card title="Budgets & thresholds">
          <p className="muted">
            This surface is read-only. Accounting computes state; authorization/admission owns
            enforcement and approval behavior.
          </p>
          {loading && !budgets ? <LoadingState /> : <UsageBudgetTable budgets={budgets?.items ?? []} />}
        </Card>
      ) : null}

      <div className="actions">
        <button disabled={loading} onClick={() => void load()}>
          {loading ? "Refreshing…" : "Refresh accounting"}
        </button>
      </div>
    </div>
  );
}

function UsageRecordTable({ records }: { records: CanonicalUsageRecord[] }) {
  if (!records.length) return <EmptyState title="No visible usage records" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Metric</th><th>Quantity</th><th>Mode</th><th>Quality</th><th>Scope</th><th>Cost</th><th>Source</th><th>Time</th></tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr key={record.id}>
              <td><strong>{record.metric_type}</strong><div><code>{record.id}</code></div></td>
              <td>{formatQuantity(record.quantity, record.unit)}</td>
              <td>{record.aggregation_mode}</td>
              <td><StatusBadge value={record.quality} /></td>
              <td>{scopeSummary(record.scope)}</td>
              <td>{formatCost(record)}</td>
              <td>{record.provider ?? record.source}</td>
              <td>{formatDate(record.timestamp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UsageAggregateTable({ aggregates }: { aggregates: CanonicalUsageAggregate[] }) {
  if (!aggregates.length) return <EmptyState title="No usage aggregates" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Metric</th><th>Value</th><th>Mode</th><th>Records</th><th>Quality mix</th><th>Recent history</th></tr></thead>
        <tbody>
          {aggregates.map((aggregate) => (
            <tr key={aggregate.id}>
              <td>{aggregate.metric_type}</td>
              <td>{formatQuantity(aggregate.total, aggregate.unit)}</td>
              <td>{aggregate.aggregation_mode}</td>
              <td>{aggregate.record_count}</td>
              <td>{qualitySummary(aggregate.quality_counts)}</td>
              <td><TrendSummary aggregate={aggregate} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UsageBudgetTable({ budgets }: { budgets: CanonicalUsageBudget[] }) {
  if (!budgets.length) return <EmptyState title="No visible usage budgets" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Budget</th><th>Scope</th><th>Consumption</th><th>Policy</th><th>Threshold</th></tr></thead>
        <tbody>
          {budgets.map((budget) => (
            <tr key={budget.id}>
              <td><strong>{budget.metric_type}</strong><div>{budget.unit} · v{budget.version}</div></td>
              <td><code>{budget.scope_type}:{budget.scope_id}</code></td>
              <td>
                <div>{formatQuantity(budget.consumed, budget.unit)} / {formatQuantity(budget.limit, budget.unit)}</div>
                {budget.fraction === null ? null : (
                  <progress max={1} value={Math.max(0, Math.min(1, budget.fraction))}>
                    {Math.round(budget.fraction * 100)}%
                  </progress>
                )}
                <small>{budget.fraction === null ? "usage unavailable" : `${Math.round(budget.fraction * 100)}% consumed`}</small>
              </td>
              <td>{budget.kind} · {budget.action}{budget.include_estimated ? " · estimates included" : ""}</td>
              <td>{budget.threshold_level ? <StatusBadge value={budget.threshold_level} /> : "within threshold"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TrendSummary({ aggregate }: { aggregate: CanonicalUsageAggregate }) {
  const sampled = aggregate.trend.filter((point) => point.value !== null).slice(-6);
  if (!sampled.length) return <span>No samples in window</span>;
  return (
    <div>
      <small>
        {aggregate.trend_bucket_seconds === null
          ? "Historical window"
          : `${formatNumber(aggregate.trend_bucket_seconds / 60)} min buckets`}
      </small>
      {sampled.map((point) => (
        <div key={point.start}>
          <time dateTime={point.start}>{formatTrendTime(point.start)}</time>{" "}
          {formatQuantity(point.value, aggregate.unit)}
        </div>
      ))}
    </div>
  );
}

function formatTrendTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
}

function qualitySummary(counts: Record<MeasurementQuality, number>): string {
  return (["measured", "reported", "estimated", "unavailable"] as MeasurementQuality[])
    .filter((quality) => counts[quality] > 0)
    .map((quality) => `${quality}:${counts[quality]}`)
    .join(" · ") || "—";
}

function scopeSummary(scope: Record<string, string>): string {
  const entries = Object.entries(scope);
  if (!entries.length) return "unscoped";
  return entries.map(([key, value]) => `${key}=${value}`).join(" · ");
}

function formatQuantity(value: number | null, unit: string): string {
  if (value === null) return "unavailable";
  return `${formatNumber(value)} ${unit}`;
}

function formatCost(record: CanonicalUsageRecord): string {
  if (record.cost_amount === null || record.currency === null) return "—";
  return `${formatNumber(record.cost_amount)} ${record.currency}`;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
