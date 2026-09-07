import { ControlPlaneError, type ControlPlaneClient } from "./client";
import type { ReferenceCollection } from "./references";

export type PlanStepStatus =
  | "pending"
  | "ready"
  | "running"
  | "waiting"
  | "succeeded"
  | "failed"
  | "skipped"
  | "cancelled";

export type CoordinationPhase =
  | "blocked"
  | "ready"
  | "attempt_active"
  | "waiting"
  | "retry_scheduled"
  | "terminal"
  | "inconsistent";

export type CoordinationWaitType = "deadline" | "approval" | "event" | "external_job";
export type CoordinationWaitState = "active" | "satisfied" | "rejected" | "expired" | "cancelled";
export type CoordinationRetryState =
  | "none"
  | "scheduled"
  | "active"
  | "completed"
  | "exhausted"
  | "not_retryable"
  | "cancelled";

export type ReconciliationDisposition =
  | "consistent"
  | "run_reconciled"
  | "wait_resumed"
  | "retry_resumed"
  | "canonical_terminal"
  | "missing_canonical_run"
  | "inconsistent";

export interface PlanCoordinationStep {
  id: string;
  status: PlanStepStatus;
  coordination_phase: CoordinationPhase;
  coordination_revision: number | null;
  dependency_ids: string[];
  satisfied_dependency_ids: string[];
  latest_run_id: string | null;
  current_attempt: number;
  retry_due_at: string | null;
  retry_state: CoordinationRetryState | null;
  retry_max_attempts: number | null;
  wait_key: string | null;
  wait_type: CoordinationWaitType | null;
  wait_state: CoordinationWaitState | null;
  wait_deadline_at: string | null;
  wait_resolved_at: string | null;
  wait_approval_id: string | null;
  wait_approval_subject_type: string | null;
  wait_approval_subject_id: string | null;
  wait_approval_action: string | null;
  wait_event_type: string | null;
  wait_correlation_key: string | null;
  wait_external_job_ref: string | null;
  reconciliation: ReconciliationDisposition;
  reconciliation_detail: string | null;
}

export interface PlanCoordinationProjection {
  id: string;
  task_id: string;
  plan_revision: number;
  steps: PlanCoordinationStep[];
}

const PLAN_COORDINATION_COLLECTION = "plan-coordination" as unknown as ReferenceCollection;
export const WORKFLOW_PROGRESS_POLL_INTERVAL_MS = 5_000;

/**
 * Read the backend-neutral coordinator projection through the versioned Control Plane.
 * The generic reference client supplies the authenticated HTTP boundary; the cast only
 * narrows the explicitly registered extension resource shape exposed by issue #384/#560.
 */
export async function getPlanCoordination(
  client: ControlPlaneClient,
  planId: string,
): Promise<PlanCoordinationProjection> {
  return client.getReference(
    PLAN_COORDINATION_COLLECTION,
    planId,
  ) as unknown as Promise<PlanCoordinationProjection>;
}

/** A canonical Plan may exist before durable coordination state is registered for it. */
export function isMissingPlanCoordinationError(error: unknown): boolean {
  return (
    error instanceof ControlPlaneError &&
    error.status === 404 &&
    error.body.code === "not_found"
  );
}

export interface WorkflowProgressPollerOptions {
  client: ControlPlaneClient;
  taskId: string;
  planId: string;
  onProjection: (projection: PlanCoordinationProjection) => void;
  onMissing: () => void;
  onError: (error: unknown) => void;
  intervalMs?: number;
}

/**
 * Bounded canonical polling fallback for coordinator-only transitions.
 *
 * Task SSE remains the preferred event-driven refresh path. The coordinator also emits
 * transitions that are not guaranteed to create a Task Event, so this poller periodically
 * reloads only the versioned `plan-coordination` projection. A monotonically increasing
 * generation prevents overlapping/out-of-order HTTP responses from moving the client back
 * to an older projection.
 */
export class WorkflowProgressPoller {
  private timer: ReturnType<typeof setInterval> | null = null;
  private generation = 0;
  private stopped = false;
  private readonly intervalMs: number;

  constructor(private readonly options: WorkflowProgressPollerOptions) {
    this.intervalMs = options.intervalMs ?? WORKFLOW_PROGRESS_POLL_INTERVAL_MS;
    if (!Number.isFinite(this.intervalMs) || this.intervalMs <= 0) {
      throw new Error("workflow progress poll interval must be positive");
    }
  }

  start(): void {
    if (this.timer !== null) return;
    this.stopped = false;
    void this.refresh();
    this.timer = setInterval(() => {
      void this.refresh();
    }, this.intervalMs);
  }

  async refresh(): Promise<void> {
    const generation = ++this.generation;
    try {
      const projection = await getPlanCoordination(this.options.client, this.options.planId);
      if (this.stopped || generation !== this.generation) return;
      if (projection.task_id !== this.options.taskId || projection.id !== this.options.planId) {
        throw new Error("Control Plane returned a workflow projection for a different Task or Plan.");
      }
      this.options.onProjection(projection);
    } catch (error) {
      if (this.stopped || generation !== this.generation) return;
      if (isMissingPlanCoordinationError(error)) {
        this.options.onMissing();
      } else {
        this.options.onError(error);
      }
    }
  }

  stop(): void {
    this.stopped = true;
    this.generation += 1;
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}
