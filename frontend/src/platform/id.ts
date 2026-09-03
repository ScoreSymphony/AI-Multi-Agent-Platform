const CANONICAL_ID = /^(goal|project|workspace|task|plan|step|run|agent|team|artifact|result|event|approval|node|worker|worker_job|tool|tool_invocation|cap|policy_scope|model_assignment)_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isCanonicalId(value: string): boolean {
  return CANONICAL_ID.test(value);
}

export function compactCanonicalId(value: string): string {
  const separator = value.indexOf("_");
  if (separator < 0 || value.length <= separator + 14) return value;
  const prefix = value.slice(0, separator);
  const uuid = value.slice(separator + 1);
  return `${prefix}_${uuid.slice(0, 8)}…${uuid.slice(-4)}`;
}
