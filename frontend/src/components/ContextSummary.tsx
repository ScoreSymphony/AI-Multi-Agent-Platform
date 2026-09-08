import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ContextInspectionClient,
  type CanonicalContextBundle,
  type CanonicalContextRunBinding,
} from "../api/context";
import { CanonicalId, Card, EmptyState, ErrorState, LoadingState } from "./States";

export function ContextSummary({ baseUrl, runId }: { baseUrl: string; runId: string }) {
  const client = useMemo(() => new ContextInspectionClient({ baseUrl }), [baseUrl]);
  const [bindings, setBindings] = useState<CanonicalContextRunBinding[]>([]);
  const [bundles, setBundles] = useState<CanonicalContextBundle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const inspection = await client.forRun(runId);
      setBindings(inspection.bindings);
      setBundles(inspection.bundles);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [client, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card title="Canonical context">
      {loading ? <LoadingState label="Loading Context Bundle evidence…" /> : null}
      {!loading && error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {!loading && !error && bindings.length === 0 ? (
        <EmptyState
          title="No Context Bundle bound"
          detail="This Run has no canonical AgentRun→Context Bundle binding."
        />
      ) : null}
      {!loading && !error && bindings.length > 0 ? (
        <div className="stack">
          {bindings.map((binding) => (
            <ContextBindingView
              key={binding.agent_run_id}
              binding={binding}
              bundle={bundles.find(
                (candidate) => candidate.context_bundle_id === binding.context_bundle_id,
              )}
            />
          ))}
        </div>
      ) : null}
    </Card>
  );
}

function ContextBindingView({
  binding,
  bundle,
}: {
  binding: CanonicalContextRunBinding;
  bundle: CanonicalContextBundle | undefined;
}) {
  return (
    <section className="stack">
      <dl>
        <div>
          <dt>AgentRun</dt>
          <dd><CanonicalId value={binding.agent_run_id} /></dd>
        </div>
        <div>
          <dt>Context Bundle</dt>
          <dd><CanonicalId value={binding.context_bundle_id} /></dd>
        </div>
        <div>
          <dt>Bundle digest</dt>
          <dd><code>{binding.context_bundle_digest}</code></dd>
        </div>
        <div>
          <dt>Agent revision</dt>
          <dd><CanonicalId value={binding.agent_id} /> r{binding.agent_revision}</dd>
        </div>
        <div>
          <dt>Resolver / policy</dt>
          <dd>{binding.resolver_version} / {binding.policy_version}</dd>
        </div>
        <div>
          <dt>Orchestrator adapter</dt>
          <dd><code>{binding.orchestrator_adapter_id}</code></dd>
        </div>
      </dl>
      {bundle ? <ContextBundleEvidence bundle={bundle} /> : null}
    </section>
  );
}

function ContextBundleEvidence({ bundle }: { bundle: CanonicalContextBundle }) {
  return (
    <div className="stack">
      <dl>
        <div>
          <dt>Plan / step</dt>
          <dd>{bundle.plan_id ?? "—"} / {bundle.step_id ?? "—"}</dd>
        </div>
        <div>
          <dt>Skill Bundle</dt>
          <dd>{bundle.skill_bundle_id ?? "—"}</dd>
        </div>
        <div>
          <dt>Budget usage</dt>
          <dd><code>{JSON.stringify(bundle.usage)}</code></dd>
        </div>
        <div>
          <dt>Budget limit</dt>
          <dd><code>{JSON.stringify(bundle.budget)}</code></dd>
        </div>
        <div>
          <dt>Reproducibility limited</dt>
          <dd>{bundle.reproducibility_limited ? "yes" : "no"}</dd>
        </div>
        <div>
          <dt>Omissions</dt>
          <dd>{bundle.omissions.length}</dd>
        </div>
      </dl>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Source</th>
              <th>Role</th>
              <th>Freshness</th>
              <th>Classification</th>
              <th>Tokens</th>
            </tr>
          </thead>
          <tbody>
            {bundle.entries.map((entry) => (
              <tr key={`${bundle.context_bundle_id}:${entry.ordinal}`}>
                <td>{entry.ordinal}</td>
                <td>
                  {entry.source_type}
                  {entry.hidden ? " (redacted)" : entry.source_id ? `: ${entry.source_id}` : ""}
                </td>
                <td>{entry.role}{entry.mandatory ? " · mandatory" : ""}</td>
                <td>{entry.freshness}</td>
                <td>{entry.data_classification}</td>
                <td>{entry.estimated_tokens}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {bundle.omissions.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Omitted source</th>
                <th>Reason</th>
                <th>Mandatory</th>
              </tr>
            </thead>
            <tbody>
              {bundle.omissions.map((omission, index) => (
                <tr key={`${bundle.context_bundle_id}:omission:${index}`}>
                  <td>{omission.source_type}</td>
                  <td>{omission.reason}</td>
                  <td>{omission.mandatory ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
