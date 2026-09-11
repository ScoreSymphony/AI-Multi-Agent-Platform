export function attentionErrorMessage(value: unknown): string {
  if (value instanceof Error) return value.message;
  if (value && typeof value === "object" && "message" in value) {
    const message = (value as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return "Canonical approval/input attention is temporarily unavailable.";
}
