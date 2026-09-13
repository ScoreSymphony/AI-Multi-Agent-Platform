export function required(form: FormData, field: string): string {
  const value = String(form.get(field) ?? "").trim();
  if (!value) throw new Error(`${field} is required.`);
  return value;
}

export function optional(form: FormData, field: string): string | undefined {
  const value = String(form.get(field) ?? "").trim();
  return value || undefined;
}

export function csv(form: FormData, field: string): string[] {
  return splitCsv(String(form.get(field) ?? ""));
}

export function splitCsv(value: string): string[] {
  return Array.from(new Set(value.split(",").map((item) => item.trim()).filter(Boolean)));
}

export function expiresAt(form: FormData): string {
  const hours = Number(form.get("expires_in_hours") ?? 72);
  const duration = Number.isFinite(hours) && hours > 0 ? Math.min(hours, 720) : 72;
  return new Date(Date.now() + duration * 60 * 60 * 1000).toISOString();
}
