export type ReferenceCollection = "plans" | "steps" | "artifacts" | "results";

export interface CanonicalPlanReference {
  id: string;
  type: "plan";
  task_id: string;
  step_ids: string[];
}

export interface CanonicalStepReference {
  id: string;
  type: "step";
  task_id: string;
  plan_id: string | null;
}

export interface CanonicalArtifactReference {
  id: string;
  type: "artifact";
  task_id: string;
}

export interface CanonicalResultReference {
  id: string;
  type: "result";
  task_id: string;
}

export type CanonicalReference =
  | CanonicalPlanReference
  | CanonicalStepReference
  | CanonicalArtifactReference
  | CanonicalResultReference;
