import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, ListQuery, Page } from "./types";

export interface CanonicalAcceleratorResource {
  accelerator_id: string;
  kind: string;
  vendor: string | null;
  model: string | null;
  memory_total_bytes: number;
  memory_available_bytes: number;
}

export interface CanonicalResourceSnapshot {
  cpu_cores_total: number;
  cpu_cores_available: number;
  ram_total_bytes: number;
  ram_available_bytes: number;
  storage_total_bytes: number;
  storage_available_bytes: number;
  accelerators: CanonicalAcceleratorResource[];
}

export interface CanonicalNode {
  id: string;
  display_name: string;
  status: string;
  registered_at: string;
  last_heartbeat_at: string;
  labels: string[];
  os_name: string | null;
  platform: string | null;
  architecture: string | null;
  resources: CanonicalResourceSnapshot;
  supported_runtimes: string[];
  model_refs: string[];
  capability_refs: string[];
  worker_refs: string[];
  trust_level: string;
  draining: boolean;
  maintenance: boolean;
  network_available: boolean;
  locality_refs: string[];
}

export interface CanonicalWorker {
  id: string;
  node_id: string;
  worker_type: string;
  supported_executors: string[];
  capability_refs: string[];
  supported_runtimes: string[];
  model_refs: string[];
  concurrency_limit: number;
  active_jobs: number;
  status: string;
  protocol_version: string;
  worker_version: string;
  registered_at: string;
  last_heartbeat_at: string;
  draining: boolean;
  locality_refs: string[];
}

export interface CanonicalWorkerJobRequirements {
  executor_type: string | null;
  capability_refs: string[];
  cpu_cores_min: number;
  ram_min_bytes: number;
  storage_min_bytes: number;
  gpu: "optional" | "required" | "forbidden" | string;
  vram_min_bytes: number;
  model_ref: string | null;
  runtime: string | null;
  os_name: string | null;
  network_required: boolean;
  required_labels: string[];
  preferred_labels: string[];
  preferred_node_ids: string[];
  preferred_worker_ids: string[];
  anti_affinity_node_ids: string[];
  allowed_trust_levels: string[];
  locality_refs: string[];
  concurrency_units: number;
}

export interface CanonicalWorkerJob {
  id: string;
  worker_id: string;
  reservation_id: string;
  state: string;
  run_id: string;
  subject_type: string;
  subject_id: string;
  workspace_ref: string | null;
  snapshot_ref: string | null;
  artifact_refs: string[];
  actor_ref: string | null;
  cancellation_ref: string | null;
  timeout_seconds: number | null;
  dispatch_attempt: number;
  idempotency_key: string;
  trace_parent: string | null;
  requirements: CanonicalWorkerJobRequirements;
  execution_status: string | null;
  last_error: string | null;
}

export interface ComputeClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const NODES = "nodes";
const WORKERS = "workers";
const WORKER_JOBS = "worker-jobs";

export class ComputeClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: ComputeClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  listNodes(query: ListQuery = {}): Promise<Page<CanonicalNode>> {
    return this.collections.list<CanonicalNode>(NODES, query);
  }

  getNode(nodeId: string): Promise<CanonicalNode> {
    return this.collections.get<CanonicalNode>(NODES, requireRef(nodeId, "node"));
  }

  listWorkers(query: ListQuery = {}): Promise<Page<CanonicalWorker>> {
    return this.collections.list<CanonicalWorker>(WORKERS, query);
  }

  getWorker(workerId: string): Promise<CanonicalWorker> {
    return this.collections.get<CanonicalWorker>(WORKERS, requireRef(workerId, "worker"));
  }

  listWorkerJobs(query: ListQuery = {}): Promise<Page<CanonicalWorkerJob>> {
    return this.collections.list<CanonicalWorkerJob>(WORKER_JOBS, query);
  }

  getWorkerJob(workerJobId: string): Promise<CanonicalWorkerJob> {
    return this.collections.get<CanonicalWorkerJob>(
      WORKER_JOBS,
      requireRef(workerJobId, "worker job"),
    );
  }

  drainNode(nodeId: string, idempotencyKey: string = crypto.randomUUID()): Promise<CanonicalNode> {
    return this.command<CanonicalNode>("node.drain", requireRef(nodeId, "node"), idempotencyKey);
  }

  undrainNode(nodeId: string, idempotencyKey: string = crypto.randomUUID()): Promise<CanonicalNode> {
    return this.command<CanonicalNode>("node.undrain", requireRef(nodeId, "node"), idempotencyKey);
  }

  enableNodeMaintenance(
    nodeId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalNode> {
    return this.command<CanonicalNode>(
      "node.maintenance-enable",
      requireRef(nodeId, "node"),
      idempotencyKey,
    );
  }

  disableNodeMaintenance(
    nodeId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalNode> {
    return this.command<CanonicalNode>(
      "node.maintenance-disable",
      requireRef(nodeId, "node"),
      idempotencyKey,
    );
  }

  drainWorker(
    workerId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalWorker> {
    return this.command<CanonicalWorker>(
      "worker.drain",
      requireRef(workerId, "worker"),
      idempotencyKey,
    );
  }

  undrainWorker(
    workerId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalWorker> {
    return this.command<CanonicalWorker>(
      "worker.undrain",
      requireRef(workerId, "worker"),
      idempotencyKey,
    );
  }

  private async command<T>(command: string, resourceRef: string, idempotencyKey: string): Promise<T> {
    if (!idempotencyKey.trim()) throw new Error("compute idempotency key is required");
    const headers = new Headers({
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
      "Idempotency-Key": idempotencyKey,
    });
    const response = await this.fetchImpl(
      `${this.baseUrl}/api/v1/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify({ resource_ref: resourceRef }),
      },
    );
    const text = await response.text();
    const payload: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, payload));
    }
    return payload as T;
  }
}

function requireRef(value: string, label: string): string {
  if (!value.trim()) throw new Error(`${label} reference is required`);
  return value;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function normalizeError(response: Response, payload: unknown): APIErrorBody {
  if (isErrorBody(payload)) return payload;
  const requestId = response.headers.get("x-request-id") ?? "unknown";
  return {
    code: "invalid_response",
    category: "contract",
    message: `Control Plane returned HTTP ${response.status} without a canonical error envelope`,
    request_id: requestId,
    correlation_id: response.headers.get("x-correlation-id") ?? requestId,
    retryable: false,
  };
}

function isErrorBody(value: unknown): value is APIErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<APIErrorBody>;
  return (
    typeof candidate.code === "string"
    && typeof candidate.category === "string"
    && typeof candidate.message === "string"
    && typeof candidate.request_id === "string"
    && typeof candidate.correlation_id === "string"
    && typeof candidate.retryable === "boolean"
  );
}
