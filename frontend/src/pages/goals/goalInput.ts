import type {
  CanonicalGoalCriterion,
  GoalEvidenceInput,
} from "../../api/goals";
import type { JsonValue } from "../../api/types";

export const TERMINAL_GOAL_STATES = new Set(["satisfied", "failed", "cancelled", "superseded"]);

export const DEFAULT_CRITERIA = JSON.stringify(
  [
    {
      criterion_id: "acceptance",
      kind: "human_acceptance",
      description: "A human explicitly accepts the Goal outcome",
      operator: "truthy",
      target: true,
      required: true,
    },
  ],
  null,
  2,
);

export function parseCriteria(raw: string): CanonicalGoalCriterion[] {
  const value = parseJson(raw, "success criteria");
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error("success criteria must be a non-empty JSON array");
  }
  return value as unknown as CanonicalGoalCriterion[];
}

export function parseEvidence(raw: string): GoalEvidenceInput[] {
  const value = parseJson(raw, "evidence");
  if (!Array.isArray(value)) throw new Error("evidence must be a JSON array");
  return value as unknown as GoalEvidenceInput[];
}

export function requireText(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} must not be blank`);
  return normalized;
}

function parseJson(raw: string, label: string): JsonValue {
  try {
    return JSON.parse(raw) as JsonValue;
  } catch (error) {
    throw new Error(`${label} must contain valid JSON`, { cause: error });
  }
}
