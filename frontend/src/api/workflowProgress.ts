import type { ControlPlaneClient } from "./client";
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
  wait_type: CoordinationWaitType | null;
  wait_deadline_at: string | null;
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

/**
 * Read the backend-neutral coordinator projection through the versioned Control Plane.
 * The generic reference client supplies the authenticated HTTP boundary; the cast only
 * narrows the explicitly registered extension resource shape exposed by issue #384.
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
