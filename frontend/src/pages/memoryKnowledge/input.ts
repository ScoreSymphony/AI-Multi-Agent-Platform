import type { JsonValue } from "../../api/types";

export function parseJsonValue(value: string, label: string): JsonValue {
  try {
    return JSON.parse(value) as JsonValue;
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
}

export function parseJsonObject(value: string, label: string): Record<string, JsonValue> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value || "{}");
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return parsed as Record<string, JsonValue>;
}

export function blankToUndefined(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

export function blankToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}
