import { useCallback, useEffect, useState } from "react";
import { ControlPlaneClient } from "../../api/client";
import { AppLink } from "../../app/router";
import type { TraceNode, TracePage } from "../../api/trace";
import {
  CanonicalId,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../../components/States";

export function TraceExplorer({
  client,
  taskId,
}: {
  client: ControlPlaneClient;
  taskId: string;
}) {
  const [trace, setTrace] = useState<TracePage | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [agentFilter, setAgentFilter] = useState("");
  const [stepFilter, setStepFilter] = useState("");
  const [modelConfigFilter, setModelConfigFilter] = useState("");
  const [modelProviderFilter, setModelProviderFilter] = useState("");
  const [capabilityFilter, setCapabilityFilter] = useState("");
  const [failureFilter, setFailureFilter] = useState("");
  const [startedAfterFilter, setStartedAfterFilter] = useState("");
  const [startedBeforeFilter, setStartedBeforeFilter] = useState("");
  const [selectedNode, setSelectedNode] = useState<TraceNode | null>(null);
  const [nodeLoading, setNodeLoading] = useState(false);
  const [nodeError, setNodeError] = useState<unknown>(null);
  const [workspaceByRunId, setWorkspaceByRunId] = useState<Record<string, string>>({});

  useEffect(() => {
    let active = true;
    if (!taskId) {
      setWorkspaceByRunId({});
      return () => {
        active = false;
      };
    }
    void client
      .listTaskRuns(taskId, { limit: 100, sort: "created_at", direction: "desc" })
      .then((page) => {
        if (!active) return;
        const bindings: Record<string, string> = {};
        for (const run of page.items) {
          if (run.workspace_id) bindings[run.id] = run.workspace_id;
        }
        setWorkspaceByRunId(bindings);
      })
      .catch(() => {
        if (active) setWorkspaceByRunId({});
      });
    return () => {
      active = false;
    };
  }, [client, taskId]);

  const loadTrace = useCallback(
    async (cursor?: string, append = false) => {
      if (!taskId) {
        setTrace(null);
        setError(null);
        return;
      }
      const filters: Record<string, string> = {};
      if (agentFilter.trim()) filters.agent_id = agentFilter.trim();
      if (stepFilter.trim()) filters.step_id = stepFilter.trim();
      if (modelConfigFilter.trim()) filters.model_config_id = modelConfigFilter.trim();
      if (modelProviderFilter.trim()) filters.model_provider_id = modelProviderFilter.trim();
      if (capabilityFilter.trim()) filters.capability_id = capabilityFilter.trim();
      if (failureFilter.trim()) filters.failure_component = failureFilter.trim();
      if (startedAfterFilter.trim()) filters.started_after = startedAfterFilter.trim();
      if (startedBeforeFilter.trim()) filters.started_before = startedBeforeFilter.trim();
      try {
        const next = await client.trace(taskId, {
          limit: 200,
          cursor,
          sort: "timestamp",
          direction: "asc",
          filters,
        });
        setTrace((current) => {
          if (!append || !current) return next;
          return { ...next, items: [...current.items, ...next.items] };
        });
        setError(null);
      } catch (nextError) {
        setError(nextError);
      }
    },
    [
      agentFilter,
      capabilityFilter,
      client,
      failureFilter,
      modelConfigFilter,
      modelProviderFilter,
      startedAfterFilter,
      startedBeforeFilter,
      stepFilter,
      taskId,
    ],
  );

  const inspectNode = useCallback(
    async (nodeId: string) => {
      if (!taskId) return;
      setNodeLoading(true);
      setNodeError(null);
      try {
        setSelectedNode(await client.getTraceNode(taskId, nodeId));
      } catch (nextError) {
        setSelectedNode(null);
        setNodeError(nextError);
      } finally {
        setNodeLoading(false);
      }
    },
    [client, taskId],
  );

  useEffect(() => {
    setTrace(null);
    void loadTrace();
  }, [loadTrace]);

  useEffect(() => {
    setSelectedNode(null);
    setNodeError(null);
    setNodeLoading(false);
  }, [taskId]);

  return (
    <>
      <Card title="Multi-agent run trace">
        <div className="toolbar">
          <label>
            Agent ID
            <input
              value={agentFilter}
              onChange={(event) => setAgentFilter(event.target.value)}
            />
          </label>
          <label>
            Step ID
            <input
              value={stepFilter}
              onChange={(event) => setStepFilter(event.target.value)}
            />
          </label>
          <label>
            Model config ID
            <input
              value={modelConfigFilter}
              onChange={(event) => setModelConfigFilter(event.target.value)}
            />
          </label>
          <label>
            Model provider ID
            <input
              value={modelProviderFilter}
              onChange={(event) => setModelProviderFilter(event.target.value)}
            />
          </label>
          <label>
            Capability ID
            <input
              value={capabilityFilter}
              onChange={(event) => setCapabilityFilter(event.target.value)}
            />
          </label>
          <label>
            Failure layer
            <input
              value={failureFilter}
              onChange={(event) => setFailureFilter(event.target.value)}
              placeholder="execution"
            />
          </label>
          <label>
            Started after
            <input
              value={startedAfterFilter}
              onChange={(event) => setStartedAfterFilter(event.target.value)}
              placeholder="2026-09-15T20:00:00+00:00"
            />
          </label>
          <label>
            Started before
            <input
              value={startedBeforeFilter}
              onChange={(event) => setStartedBeforeFilter(event.target.value)}
              placeholder="2026-09-15T21:00:00+00:00"
            />
          </label>
          <button onClick={() => void loadTrace()}>Apply filters</button>
        </div>
        {trace ? (
          <p>
            Telemetry <StatusBadge value={trace.telemetry_state} />
            {trace.missing_sources.length > 0
              ? ` · missing: ${trace.missing_sources.join(", ")}`
              : ""}
          </p>
        ) : null}
        {error ? <ErrorState error={error} onRetry={() => void loadTrace()} /> : null}
        {trace === null ? (
          <LoadingState />
        ) : trace.items.length === 0 ? (
          <EmptyState title="No trace nodes for this Task" />
        ) : (
          <TraceTable
            items={trace.items}
            selectedNodeId={selectedNode?.id ?? null}
            onInspect={inspectNode}
            workspaceByRunId={workspaceByRunId}
          />
        )}
        {trace?.next_cursor ? (
          <button onClick={() => void loadTrace(trace.next_cursor ?? undefined, true)}>
            Load more trace nodes
          </button>
        ) : null}
      </Card>
      {trace && trace.task_usage.length > 0 ? (
        <Card title="Task-level usage">
          <UsageTable items={trace.task_usage} />
        </Card>
      ) : null}
      {nodeLoading || nodeError || selectedNode ? (
        <Card title="Trace node detail">
          {nodeLoading ? <LoadingState /> : null}
          {nodeError ? (
            <ErrorState
              error={nodeError}
              onRetry={
                selectedNode ? () => void inspectNode(selectedNode.id) : undefined
              }
            />
          ) : null}
          {!nodeLoading && !nodeError && selectedNode ? (
            <TraceNodeDetail node={selectedNode} workspaceByRunId={workspaceByRunId} />
          ) : null}
        </Card>
      ) : null}
    </>
  );
}

function TraceTable({
  items,
  selectedNodeId,
  onInspect,
  workspaceByRunId,
}: {
  items: TraceNode[];
  selectedNodeId: string | null;
  onInspect: (nodeId: string) => void | Promise<void>;
  workspaceByRunId: Record<string, string>;
}) {
  const byId = new Map(items.map((item) => [item.id, item]));
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Execution</th>
            <th>Outcome</th>
            <th>Duration</th>
            <th>Context</th>
            <th>Usage / links</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const depth = traceDepth(item, byId);
            return (
              <tr key={item.id}>
                <td>
                  <div style={{ paddingLeft: `${depth * 18}px` }}>
                    <strong>{item.name}</strong> <StatusBadge value={item.kind} />
                    {item.parallel_with.length > 0 ? (
                      <span> · parallel ×{item.parallel_with.length}</span>
                    ) : null}
                    <div>
                      <CanonicalId value={item.id} />{" "}
                      <button type="button" onClick={() => void onInspect(item.id)}>
                        {selectedNodeId === item.id ? "Inspecting" : "Inspect"}
                      </button>
                    </div>
                  </div>
                </td>
                <td>
                  <StatusBadge value={item.outcome} />
                  {item.failure ? (
                    <div>
                      {item.failure.component}: {item.failure.code}
                      {item.failure.retryable ? " · retryable" : ""}
                    </div>
                  ) : null}
                </td>
                <td>{formatDuration(item.duration_seconds)}</td>
                <td>
                  <TraceContext node={item} workspaceId={item.context.run_id ? workspaceByRunId[item.context.run_id] : undefined} />
                </td>
                <td>
                  {item.usage.length > 0 ? (
                    <UsageTable items={item.usage} compact />
                  ) : (
                    "—"
                  )}
                  {item.resources.length > 0 ? (
                    <div>
                      {item.resources.map((resource) => (
                        <span key={`${resource.type}:${resource.id}`}>
                          <a href={resource.href}>{resource.type}</a>{" "}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TraceNodeDetail({
  node,
  workspaceByRunId,
}: {
  node: TraceNode;
  workspaceByRunId: Record<string, string>;
}) {
  return (
    <div>
      <p>
        <strong>{node.name}</strong> <StatusBadge value={node.outcome} />{" "}
        <StatusBadge value={node.kind} />
      </p>
      <p>
        Node <CanonicalId value={node.id} />
        {node.trace_id ? (
          <>
            {" "}· Trace <CanonicalId value={node.trace_id} />
          </>
        ) : null}
        {node.parent_id ? (
          <>
            {" "}· Parent <CanonicalId value={node.parent_id} />
          </>
        ) : null}
      </p>
      <p>
        Started {node.timestamp}
        {node.finished_at ? ` · finished ${node.finished_at}` : ""}
        {node.duration_seconds !== null
          ? ` · ${formatDuration(node.duration_seconds)}`
          : ""}
      </p>
      <p>
        <TraceContext node={node} workspaceId={node.context.run_id ? workspaceByRunId[node.context.run_id] : undefined} />
      </p>
      {node.failure ? (
        <p>
          Failure: {node.failure.component} / {node.failure.code}
          {node.failure.retryable ? " · retryable" : ""}
        </p>
      ) : null}
      {node.parallel_with.length > 0 ? (
        <p>Parallel with: {node.parallel_with.join(", ")}</p>
      ) : null}
      {node.resources.length > 0 ? (
        <div>
          <strong>Canonical resources</strong>
          <ul>
            {node.resources.map((resource) => (
              <li key={`${resource.type}:${resource.id}`}>
                <a href={resource.href}>
                  {resource.type}: {resource.id}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {node.usage.length > 0 ? (
        <div>
          <strong>Usage</strong>
          <UsageTable items={node.usage} />
        </div>
      ) : null}
      {Object.keys(node.attributes).length > 0 ? (
        <details>
          <summary>Safe diagnostic attributes</summary>
          <pre>{JSON.stringify(node.attributes, null, 2)}</pre>
        </details>
      ) : null}
      {node.async_links.length > 0 ? (
        <details>
          <summary>Asynchronous trace links</summary>
          <pre>{JSON.stringify(node.async_links, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  );
}

export function TraceContext({
  node,
  workspaceId,
}: {
  node: TraceNode;
  workspaceId?: string;
}) {
  const canonicalWorkspaceId = node.context.workspace_id ?? workspaceId;
  const preferred = [
    "step_id",
    "agent_id",
    "team_id",
    "model_provider_id",
    "model_config_id",
    "capability_id",
    "worker_id",
    "approval_id",
    "verification_id",
  ];
  const visible = preferred.flatMap((key) =>
    node.context[key] ? [`${key.replace("_id", "")}: ${node.context[key]}`] : [],
  );
  return (
    <span>
      {canonicalWorkspaceId ? (
        <>
          workspace: <AppLink href={`/workspaces/${canonicalWorkspaceId}`}><CanonicalId value={canonicalWorkspaceId} /></AppLink>
          {visible.length > 0 ? " · " : ""}
        </>
      ) : null}
      {visible.length > 0 ? visible.join(" · ") : canonicalWorkspaceId ? null : "Task-scoped"}
    </span>
  );
}

function UsageTable({
  items,
  compact = false,
}: {
  items: TracePage["task_usage"];
  compact?: boolean;
}) {
  if (compact) {
    return (
      <>
        {items.map((item) => (
          <div key={item.id}>
            {item.metric_type}: {formatQuantity(item.quantity, item.unit)}{" "}
            <small>({item.quality})</small>
            {item.cost_amount !== null
              ? ` · ${item.cost_amount} ${item.currency ?? ""}`
              : ""}
          </div>
        ))}
      </>
    );
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Metric</th>
            <th>Value</th>
            <th>Quality</th>
            <th>Source</th>
            <th>Cost</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{item.metric_type}</td>
              <td>{formatQuantity(item.quantity, item.unit)}</td>
              <td>
                <StatusBadge value={item.quality} />
              </td>
              <td>{item.source}</td>
              <td>
                {item.cost_amount === null
                  ? "—"
                  : `${item.cost_amount} ${item.currency ?? ""}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function traceDepth(node: TraceNode, byId: Map<string, TraceNode>): number {
  let depth = 0;
  let current = node.parent_id ? byId.get(node.parent_id) : undefined;
  const seen = new Set<string>([node.id]);
  while (current && !seen.has(current.id) && depth < 20) {
    seen.add(current.id);
    depth += 1;
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  return depth;
}

function formatDuration(value: number | null): string {
  if (value === null) return "—";
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  return `${value.toFixed(2)} s`;
}

function formatQuantity(value: number | null, unit: string): string {
  return value === null ? `unavailable ${unit}` : `${value} ${unit}`;
}
