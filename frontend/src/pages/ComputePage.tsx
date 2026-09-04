import { useCallback, useEffect, useState } from "react";
import {
  ComputeClient,
  type CanonicalAcceleratorResource,
  type CanonicalNode,
  type CanonicalWorker,
  type CanonicalWorkerJob,
  type CanonicalWorkerJobRequirements,
} from "../api/compute";
import type { Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  CanonicalId,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const NODE_QUERY_KEY = "compute:nodes";
const WORKER_QUERY_KEY = "compute:workers";
const WORKER_JOB_QUERY_KEY = "compute:worker-jobs";

export function ComputePage({ client }: { client: ComputeClient }) {
  const [nodes, setNodes] = useState<Page<CanonicalNode> | null>(null);
  const [workers, setWorkers] = useState<Page<CanonicalWorker> | null>(null);
  const [workerJobs, setWorkerJobs] = useState<Page<CanonicalWorkerJob> | null>(null);
  const [nodeError, setNodeError] = useState<unknown>(null);
  const [workerError, setWorkerError] = useState<unknown>(null);
  const [workerJobError, setWorkerJobError] = useState<unknown>(null);
  const nodePagination = useCursorPagination(NODE_QUERY_KEY);
  const workerPagination = useCursorPagination(WORKER_QUERY_KEY);
  const workerJobPagination = useCursorPagination(WORKER_JOB_QUERY_KEY);

  const loadNodes = useCallback(async () => {
    try {
      setNodes(await client.listNodes({ limit: 50, cursor: nodePagination.cursor }));
      setNodeError(null);
    } catch (error) {
      setNodeError(error);
    }
  }, [client, nodePagination.cursor]);

  const loadWorkers = useCallback(async () => {
    try {
      setWorkers(await client.listWorkers({ limit: 50, cursor: workerPagination.cursor }));
      setWorkerError(null);
    } catch (error) {
      setWorkerError(error);
    }
  }, [client, workerPagination.cursor]);

  const loadWorkerJobs = useCallback(async () => {
    try {
      setWorkerJobs(await client.listWorkerJobs({ limit: 50, cursor: workerJobPagination.cursor }));
      setWorkerJobError(null);
    } catch (error) {
      setWorkerJobError(error);
    }
  }, [client, workerJobPagination.cursor]);

  useEffect(() => void loadNodes(), [loadNodes]);
  useEffect(() => void loadWorkers(), [loadWorkers]);
  useEffect(() => void loadWorkerJobs(), [loadWorkerJobs]);

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Distributed runtime</p>
        <h1>Compute</h1>
        <p>
          Canonical Node, Worker and Worker Job state from the Control Plane. Infrastructure- and
          provider-private runtime identity stays behind the platform boundary.
        </p>
      </header>

      <Card title="Nodes">
        <p>Participating compute devices, health, resources, capabilities and scheduling state.</p>
        {nodeError ? <ErrorState error={nodeError} onRetry={() => void loadNodes()} /> : null}
        {!nodes ? <LoadingState label="Loading Nodes…" /> : (
          <>
            <NodeTable nodes={nodes.items} />
            <PaginationControls
              page={nodes}
              pageNumber={nodePagination.pageNumber}
              hasPrevious={nodePagination.hasPrevious}
              onPrevious={nodePagination.previous}
              onRefresh={() => void loadNodes()}
              onNext={() => nodePagination.next(nodes.next_cursor)}
            />
          </>
        )}
      </Card>

      <Card title="Workers">
        <p>Schedulable processes/services attached to canonical Nodes.</p>
        {workerError ? <ErrorState error={workerError} onRetry={() => void loadWorkers()} /> : null}
        {!workers ? <LoadingState label="Loading Workers…" /> : (
          <>
            <WorkerTable workers={workers.items} />
            <PaginationControls
              page={workers}
              pageNumber={workerPagination.pageNumber}
              hasPrevious={workerPagination.hasPrevious}
              onPrevious={workerPagination.previous}
              onRefresh={() => void loadWorkers()}
              onNext={() => workerPagination.next(workers.next_cursor)}
            />
          </>
        )}
      </Card>

      <Card title="Worker jobs">
        <p>Canonical dispatch ownership and reconciliation evidence without secret references.</p>
        {workerJobError ? (
          <ErrorState error={workerJobError} onRetry={() => void loadWorkerJobs()} />
        ) : null}
        {!workerJobs ? <LoadingState label="Loading Worker Jobs…" /> : (
          <>
            <WorkerJobTable workerJobs={workerJobs.items} />
            <PaginationControls
              page={workerJobs}
              pageNumber={workerJobPagination.pageNumber}
              hasPrevious={workerJobPagination.hasPrevious}
              onPrevious={workerJobPagination.previous}
              onRefresh={() => void loadWorkerJobs()}
              onNext={() => workerJobPagination.next(workerJobs.next_cursor)}
            />
          </>
        )}
      </Card>
    </div>
  );
}

export function ComputeNodeDetailPage({ client, nodeId }: { client: ComputeClient; nodeId: string }) {
  const [node, setNode] = useState<CanonicalNode | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setNode(await client.getNode(nodeId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, nodeId]);

  useEffect(() => void load(), [load]);

  const mutate = async (action: () => Promise<CanonicalNode>) => {
    setBusy(true);
    setActionError(null);
    try {
      setNode(await action());
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!node) return <LoadingState label="Loading Node…" />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Compute / Node</p>
        <h1>{node.display_name}</h1>
        <p><CanonicalId value={node.id} /></p>
      </header>
      {actionError ? <ErrorState error={actionError} /> : null}

      <Card title="Runtime state">
        <dl className="detail-grid">
          <Detail label="Status"><StatusBadge value={node.status} /></Detail>
          <Detail label="Trust level">{node.trust_level}</Detail>
          <Detail label="Draining">{yesNo(node.draining)}</Detail>
          <Detail label="Maintenance">{yesNo(node.maintenance)}</Detail>
          <Detail label="Network available">{yesNo(node.network_available)}</Detail>
          <Detail label="Registered">{formatTimestamp(node.registered_at)}</Detail>
          <Detail label="Last heartbeat">{formatTimestamp(node.last_heartbeat_at)}</Detail>
        </dl>
        <div className="button-row" aria-label="Node administration">
          <button
            disabled={busy}
            onClick={() => void mutate(() => node.draining ? client.undrainNode(node.id) : client.drainNode(node.id))}
          >
            {node.draining ? "Stop draining" : "Drain Node"}
          </button>
          <button
            disabled={busy}
            onClick={() => void mutate(() => node.maintenance
              ? client.disableNodeMaintenance(node.id)
              : client.enableNodeMaintenance(node.id))}
          >
            {node.maintenance ? "Disable maintenance" : "Enable maintenance"}
          </button>
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
        </div>
        <p className="muted">
          These controls invoke only the canonical #14 administrative commands. Server authorization
          remains authoritative.
        </p>
      </Card>

      <Card title="Resources">
        <dl className="detail-grid">
          <Detail label="CPU">{node.resources.cpu_cores_available} / {node.resources.cpu_cores_total} cores available</Detail>
          <Detail label="RAM">{formatBytes(node.resources.ram_available_bytes)} / {formatBytes(node.resources.ram_total_bytes)} available</Detail>
          <Detail label="Storage">{formatBytes(node.resources.storage_available_bytes)} / {formatBytes(node.resources.storage_total_bytes)} available</Detail>
        </dl>
        <AcceleratorTable accelerators={node.resources.accelerators} />
      </Card>

      <Card title="Platform metadata">
        <dl className="detail-grid">
          <Detail label="OS">{node.os_name ?? "—"}</Detail>
          <Detail label="Platform">{node.platform ?? "—"}</Detail>
          <Detail label="Architecture">{node.architecture ?? "—"}</Detail>
          <Detail label="Labels"><TextList values={node.labels} /></Detail>
          <Detail label="Runtimes"><TextList values={node.supported_runtimes} /></Detail>
          <Detail label="Models"><TextList values={node.model_refs} /></Detail>
          <Detail label="Capabilities"><TextList values={node.capability_refs} /></Detail>
          <Detail label="Locality refs"><TextList values={node.locality_refs} /></Detail>
        </dl>
      </Card>

      <Card title="Workers">
        <CanonicalLinks
          values={node.worker_refs}
          href={(workerId) => `/compute/workers/${encodeURIComponent(workerId)}`}
          empty="No Workers are currently referenced by this Node."
        />
      </Card>
    </div>
  );
}

export function ComputeWorkerDetailPage({
  client,
  workerId,
}: {
  client: ComputeClient;
  workerId: string;
}) {
  const [worker, setWorker] = useState<CanonicalWorker | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setWorker(await client.getWorker(workerId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, workerId]);

  useEffect(() => void load(), [load]);

  const mutate = async () => {
    if (!worker) return;
    setBusy(true);
    setActionError(null);
    try {
      setWorker(worker.draining
        ? await client.undrainWorker(worker.id)
        : await client.drainWorker(worker.id));
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!worker) return <LoadingState label="Loading Worker…" />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Compute / Worker</p>
        <h1><CanonicalId value={worker.id} /></h1>
        <p>{worker.worker_type}</p>
      </header>
      {actionError ? <ErrorState error={actionError} /> : null}

      <Card title="Runtime state">
        <dl className="detail-grid">
          <Detail label="Status"><StatusBadge value={worker.status} /></Detail>
          <Detail label="Node">
            <AppLink href={`/compute/nodes/${encodeURIComponent(worker.node_id)}`}>
              <CanonicalId value={worker.node_id} />
            </AppLink>
          </Detail>
          <Detail label="Active jobs">{worker.active_jobs} / {worker.concurrency_limit}</Detail>
          <Detail label="Draining">{yesNo(worker.draining)}</Detail>
          <Detail label="Protocol">{worker.protocol_version}</Detail>
          <Detail label="Worker version">{worker.worker_version}</Detail>
          <Detail label="Registered">{formatTimestamp(worker.registered_at)}</Detail>
          <Detail label="Last heartbeat">{formatTimestamp(worker.last_heartbeat_at)}</Detail>
        </dl>
        <div className="button-row" aria-label="Worker administration">
          <button disabled={busy} onClick={() => void mutate()}>
            {worker.draining ? "Stop draining" : "Drain Worker"}
          </button>
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
        </div>
      </Card>

      <Card title="Capabilities and placement metadata">
        <dl className="detail-grid">
          <Detail label="Executors"><TextList values={worker.supported_executors} /></Detail>
          <Detail label="Capabilities"><TextList values={worker.capability_refs} /></Detail>
          <Detail label="Runtimes"><TextList values={worker.supported_runtimes} /></Detail>
          <Detail label="Models"><TextList values={worker.model_refs} /></Detail>
          <Detail label="Locality refs"><TextList values={worker.locality_refs} /></Detail>
        </dl>
      </Card>
    </div>
  );
}

export function ComputeWorkerJobDetailPage({
  client,
  workerJobId,
}: {
  client: ComputeClient;
  workerJobId: string;
}) {
  const [workerJob, setWorkerJob] = useState<CanonicalWorkerJob | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setWorkerJob(await client.getWorkerJob(workerJobId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, workerJobId]);

  useEffect(() => void load(), [load]);

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!workerJob) return <LoadingState label="Loading Worker Job…" />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Compute / Worker Job</p>
        <h1><CanonicalId value={workerJob.id} /></h1>
        <p>Dispatch ownership and reconciliation evidence.</p>
      </header>

      <Card title="Dispatch state">
        <dl className="detail-grid">
          <Detail label="State"><StatusBadge value={workerJob.state} /></Detail>
          <Detail label="Execution status">{workerJob.execution_status ? <StatusBadge value={workerJob.execution_status} /> : "—"}</Detail>
          <Detail label="Worker">
            <AppLink href={`/compute/workers/${encodeURIComponent(workerJob.worker_id)}`}>
              <CanonicalId value={workerJob.worker_id} />
            </AppLink>
          </Detail>
          <Detail label="Run">
            <AppLink href={`/runs/${encodeURIComponent(workerJob.run_id)}`}>
              <CanonicalId value={workerJob.run_id} />
            </AppLink>
          </Detail>
          <Detail label="Subject">{renderSubject(workerJob.subject_type, workerJob.subject_id)}</Detail>
          <Detail label="Reservation"><CanonicalId value={workerJob.reservation_id} /></Detail>
          <Detail label="Dispatch attempt">{workerJob.dispatch_attempt}</Detail>
          <Detail label="Timeout">{workerJob.timeout_seconds === null ? "—" : `${workerJob.timeout_seconds}s`}</Detail>
          <Detail label="Last error">{workerJob.last_error ?? "—"}</Detail>
        </dl>
      </Card>

      <Card title="Canonical references">
        <dl className="detail-grid">
          <Detail label="Workspace">{renderWorkspace(workerJob.workspace_ref)}</Detail>
          <Detail label="Snapshot">{workerJob.snapshot_ref ? <CanonicalId value={workerJob.snapshot_ref} /> : "—"}</Detail>
          <Detail label="Artifacts">
            <CanonicalLinks
              values={workerJob.artifact_refs}
              href={(artifactId) => `/artifacts/${encodeURIComponent(artifactId)}`}
              empty="—"
            />
          </Detail>
          <Detail label="Actor">{workerJob.actor_ref ?? "—"}</Detail>
          <Detail label="Cancellation ref">{workerJob.cancellation_ref ?? "—"}</Detail>
          <Detail label="Idempotency key"><code>{workerJob.idempotency_key}</code></Detail>
          <Detail label="Trace parent">{workerJob.trace_parent ?? "—"}</Detail>
        </dl>
      </Card>

      <Card title="Scheduling requirements">
        <Requirements requirements={workerJob.requirements} />
      </Card>
    </div>
  );
}

function NodeTable({ nodes }: { nodes: CanonicalNode[] }) {
  if (nodes.length === 0) return <EmptyState title="No Nodes" detail="No compute devices are currently registered." />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Node</th><th>Status</th><th>Available resources</th><th>Workers</th><th>Mode</th></tr></thead>
        <tbody>{nodes.map((node) => (
          <tr key={node.id}>
            <td>
              <AppLink href={`/compute/nodes/${encodeURIComponent(node.id)}`}>{node.display_name}</AppLink>
              <br /><CanonicalId value={node.id} />
            </td>
            <td><StatusBadge value={node.status} /></td>
            <td>{node.resources.cpu_cores_available} CPU · {formatBytes(node.resources.ram_available_bytes)} RAM</td>
            <td>{node.worker_refs.length}</td>
            <td>{node.maintenance ? "maintenance" : node.draining ? "draining" : "schedulable"}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function WorkerTable({ workers }: { workers: CanonicalWorker[] }) {
  if (workers.length === 0) return <EmptyState title="No Workers" detail="No schedulable Workers are currently registered." />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Worker</th><th>Status</th><th>Node</th><th>Capacity</th><th>Type</th></tr></thead>
        <tbody>{workers.map((worker) => (
          <tr key={worker.id}>
            <td><AppLink href={`/compute/workers/${encodeURIComponent(worker.id)}`}><CanonicalId value={worker.id} /></AppLink></td>
            <td><StatusBadge value={worker.status} /></td>
            <td><AppLink href={`/compute/nodes/${encodeURIComponent(worker.node_id)}`}><CanonicalId value={worker.node_id} /></AppLink></td>
            <td>{worker.active_jobs} / {worker.concurrency_limit}{worker.draining ? " · draining" : ""}</td>
            <td>{worker.worker_type}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function WorkerJobTable({ workerJobs }: { workerJobs: CanonicalWorkerJob[] }) {
  if (workerJobs.length === 0) return <EmptyState title="No Worker Jobs" detail="No distributed dispatch records are currently exposed." />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Worker Job</th><th>State</th><th>Worker</th><th>Run</th><th>Execution</th></tr></thead>
        <tbody>{workerJobs.map((workerJob) => (
          <tr key={workerJob.id}>
            <td><AppLink href={`/compute/jobs/${encodeURIComponent(workerJob.id)}`}><CanonicalId value={workerJob.id} /></AppLink></td>
            <td><StatusBadge value={workerJob.state} /></td>
            <td><AppLink href={`/compute/workers/${encodeURIComponent(workerJob.worker_id)}`}><CanonicalId value={workerJob.worker_id} /></AppLink></td>
            <td><AppLink href={`/runs/${encodeURIComponent(workerJob.run_id)}`}><CanonicalId value={workerJob.run_id} /></AppLink></td>
            <td>{workerJob.execution_status ?? "—"}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function AcceleratorTable({ accelerators }: { accelerators: CanonicalAcceleratorResource[] }) {
  if (accelerators.length === 0) return <EmptyState title="No accelerators" detail="This Node reports no accelerator resources." />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Accelerator</th><th>Kind</th><th>Vendor / model</th><th>Memory</th></tr></thead>
        <tbody>{accelerators.map((accelerator) => (
          <tr key={accelerator.accelerator_id}>
            <td><CanonicalId value={accelerator.accelerator_id} /></td>
            <td>{accelerator.kind}</td>
            <td>{[accelerator.vendor, accelerator.model].filter(Boolean).join(" · ") || "—"}</td>
            <td>{formatBytes(accelerator.memory_available_bytes)} / {formatBytes(accelerator.memory_total_bytes)} available</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function Requirements({ requirements }: { requirements: CanonicalWorkerJobRequirements }) {
  return (
    <dl className="detail-grid">
      <Detail label="Executor">{requirements.executor_type ?? "—"}</Detail>
      <Detail label="CPU minimum">{requirements.cpu_cores_min}</Detail>
      <Detail label="RAM minimum">{formatBytes(requirements.ram_min_bytes)}</Detail>
      <Detail label="Storage minimum">{formatBytes(requirements.storage_min_bytes)}</Detail>
      <Detail label="GPU">{requirements.gpu}</Detail>
      <Detail label="VRAM minimum">{formatBytes(requirements.vram_min_bytes)}</Detail>
      <Detail label="Model">{requirements.model_ref ?? "—"}</Detail>
      <Detail label="Runtime">{requirements.runtime ?? "—"}</Detail>
      <Detail label="OS">{requirements.os_name ?? "—"}</Detail>
      <Detail label="Network required">{yesNo(requirements.network_required)}</Detail>
      <Detail label="Concurrency units">{requirements.concurrency_units}</Detail>
      <Detail label="Capabilities"><TextList values={requirements.capability_refs} /></Detail>
      <Detail label="Required labels"><TextList values={requirements.required_labels} /></Detail>
      <Detail label="Preferred labels"><TextList values={requirements.preferred_labels} /></Detail>
      <Detail label="Preferred Nodes"><TextList values={requirements.preferred_node_ids} /></Detail>
      <Detail label="Preferred Workers"><TextList values={requirements.preferred_worker_ids} /></Detail>
      <Detail label="Anti-affinity Nodes"><TextList values={requirements.anti_affinity_node_ids} /></Detail>
      <Detail label="Allowed trust levels"><TextList values={requirements.allowed_trust_levels} /></Detail>
      <Detail label="Locality refs"><TextList values={requirements.locality_refs} /></Detail>
    </dl>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return <><dt>{label}</dt><dd>{children}</dd></>;
}

function TextList({ values }: { values: string[] }) {
  return values.length === 0 ? <>—</> : <>{values.join(", ")}</>;
}

function CanonicalLinks({
  values,
  href,
  empty,
}: {
  values: string[];
  href: (value: string) => string;
  empty: string;
}) {
  if (values.length === 0) return <>{empty}</>;
  return (
    <ul className="compact-list">
      {values.map((value) => (
        <li key={value}><AppLink href={href(value)}><CanonicalId value={value} /></AppLink></li>
      ))}
    </ul>
  );
}

function renderSubject(subjectType: string, subjectId: string) {
  const href = subjectType === "task"
    ? `/tasks/${encodeURIComponent(subjectId)}`
    : subjectType === "run"
      ? `/runs/${encodeURIComponent(subjectId)}`
      : null;
  if (!href) return <><span>{subjectType}</span> · <CanonicalId value={subjectId} /></>;
  return <><span>{subjectType}</span> · <AppLink href={href}><CanonicalId value={subjectId} /></AppLink></>;
}

function renderWorkspace(workspaceRef: string | null) {
  if (!workspaceRef) return <>—</>;
  return <AppLink href={`/workspaces/${encodeURIComponent(workspaceRef)}`}><CanonicalId value={workspaceRef} /></AppLink>;
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return value === 0 ? "0 B" : String(value);
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function yesNo(value: boolean): string {
  return value ? "yes" : "no";
}
