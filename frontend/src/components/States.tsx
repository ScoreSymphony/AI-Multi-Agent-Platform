import type { ReactNode } from "react";
import { isControlPlaneError } from "../api/client";
import { describeError } from "../api/errorPresentation";
import { compactCanonicalId } from "../platform/id";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return <div className="state state-loading" aria-live="polite">{label}</div>;
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="state">
      <strong>{title}</strong>
      {detail && <p>{detail}</p>}
    </div>
  );
}

export function DegradedState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="state state-warning" role="status">
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const presentation = describeError(error);
  return (
    <div className="state state-error" role="alert">
      <strong>{presentation.title}</strong>
      <p>{presentation.message}</p>
      {presentation.hint && <p>{presentation.hint}</p>}
      {presentation.reference && <p>Reference <CanonicalId value={presentation.reference} /></p>}
      {isControlPlaneError(error) && <small>Request {error.body.request_id}</small>}
      {onRetry && <button onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function StatusBadge({ value }: { value: string }) {
  return <span className={`status status-${value.replaceAll("_", "-")}`}>{value}</span>;
}

export function CanonicalId({ value }: { value: string }) {
  return <code title={value}>{compactCanonicalId(value)}</code>;
}

export function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {children}
    </section>
  );
}
