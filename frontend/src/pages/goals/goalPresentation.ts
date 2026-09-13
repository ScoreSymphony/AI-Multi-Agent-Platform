import type { JsonValue } from "../../api/types";

export function renderJson(value: JsonValue): string {
  return JSON.stringify(value);
}

export function formatGoalDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
