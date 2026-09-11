import type { ReactNode } from "react";

export function Detail({ label, children }: { label: string; children: ReactNode }) {
  return <><dt>{label}</dt><dd>{children}</dd></>;
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="code-block"><code>{JSON.stringify(value, null, 2)}</code></pre>;
}

export function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
