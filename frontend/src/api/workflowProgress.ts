import type { ControlPlaneClient } from "./client";
import type { ReferenceCollection } from "./references";

export interface PlanCoordinationStep {
  id: string;
  status: string;
  coordination_phase: string;
  coordination_revision: number | null;
  dependency_ids: string[];
  satisfied_dependency_ids: string[];
  latest_run_id: string | null;
  current_attempt: number;
  retry_due_at: string | null;
  wait_type: string | null;
  wait_deadline_at: string | null;
  reconciliation: string;
  reconciliation_detail: string | null;
}

export interface PlanCoordinationProjection {
  id: string;
  task_id: string;
  plan_revision: number;
  steps: PlanCoordinationStep[];
}

const PLAN_COORDINATION_COLLECTION = "plan-coordination" as ReferenceCollection;

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
