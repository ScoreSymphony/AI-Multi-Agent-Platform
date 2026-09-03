import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { ControlPlaneClient } from "../api/client";
import type { CanonicalReference, ReferenceCollection } from "../api/references";
import type { Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  CanonicalId,
  Card,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";
import { isCanonicalId } from "../platform/id";

const collections: ReferenceCollection[] = ["artifacts", "results", "plans", "steps"];

export function ReferencesPage({ client }: { client: ControlPlaneClient }) {
  const [collection, setCollection] = useState<ReferenceCollection>("artifacts");
  const [page, setPage] = useState<Page<CanonicalReference> | null>(null);
  const [totals, setTotals] = useState<Record<ReferenceCollection, number> | null>(null);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [error, setError] = useState<unknown>(null);
  const queryKey = `${collection}:${appliedQuery}`;
  const pagination = useCursorPagination(queryKey);

  const loadTotals = useCallback(async () => {
    const pages = await Promise.all(collections.map((item) => client.listReferences(item, { limit: 1 })));
    setTotals({
      artifacts: pages[0].total,
      results: pages[1].total,
      plans: pages[2].total,
      steps: pages[3].total,
    });
  }, [client]);

  const load = useCallback(async () => {
    try {
      const next = await client.listReferences(collection, {
        limit: 100,
        cursor: pagination.cursor,
        q: appliedQuery || undefined,
      });
      setPage(next);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [appliedQuery, client, collection, pagination.cursor]);

  useEffect(() => {
    setPage(null);
    void load();
  }, [load]);

  useEffect(() => {
    void loadTotals().catch(() => setTotals(null));
  }, [loadTotals]);

  const search = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalized = query.trim();
    if (normalized !== appliedQuery) {
      setAppliedQuery(normalized);
      return;
    }
    if (pagination.cursor) {
      pagination.reset();
      return;
    }
    void load();
  };

  const clear = () => {
    setQuery("");
    if (appliedQuery) {
      setAppliedQuery("");
      return;
    }
    if (pagination.cursor) {
      pagination.reset();
      return;
    }
    void load();
  };

  const selectCollection = (item: ReferenceCollection) => {
    setQuery("");
    setAppliedQuery("");
    setCollection(item);
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical references</p>
        <h1>Files & artifacts</h1>
        <p>Read-only platform references for artifacts, results, plans and steps exposed by the versioned Control Plane.</p>
      </header>

      <DegradedState
        title="Artifact identity is available; file content is not a northbound resource yet"
        detail="The current canonical /artifacts collection exposes artifact IDs and owning Task IDs. This UI intentionally does not bypass the Control Plane to browse storage providers, filesystem paths or raw file bytes."
      />

      <div className="metrics">
        {collections.map((item) => <Metric key={item} label={labelFor(item)} value={totals?.[item] ?? "—"} />)}
      </div>

      <Card title="Reference collection">
        <div className="actions" role="group" aria-label="Reference collection">
          {collections.map((item) => (
            <button
              className={item === collection ? "primary" : undefined}
              key={item}
              onClick={() => selectCollection(item)}
              type="button"
            >
              {labelFor(item)}
            </button>
          ))}
        </div>
        <form className="filter-row" onSubmit={search}>
          <label>Search<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Canonical ID or task ID" /></label>
          <button type="submit">Search</button>
          <button type="button" onClick={clear}>Clear</button>
        </form>
      </Card>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <Card title={labelFor(collection)}>
        {!page ? <LoadingState /> : <ReferenceTable items={page.items} />}
      </Card>
      {page ? (
        <PaginationControls
          page={page}
          pageNumber={pagination.pageNumber}
          hasPrevious={pagination.hasPrevious}
          onPrevious={pagination.previous}
          onRefresh={() => void load()}
          onNext={() => pagination.next(page.next_cursor)}
        />
      ) : null}
    </div>
  );
}

export function ReferenceDetailPage({
  client,
  collection,
  resourceId,
}: {
  client: ControlPlaneClient;
  collection: ReferenceCollection;
  resourceId: string;
}) {
  const [resource, setResource] = useState<CanonicalReference | null>(null);
  const [error, setError] = useState<unknown>(null);

  const expectedPrefix = collection === "artifacts" ? "artifact_" : collection === "results" ? "result_" : collection === "plans" ? "plan_" : "step_";
  const routeIsCanonical = useMemo(() => isCanonicalId(resourceId) && resourceId.startsWith(expectedPrefix), [expectedPrefix, resourceId]);

  const load = useCallback(async () => {
    if (!routeIsCanonical) {
      setError(new Error(`This route does not contain a canonical ${collection.slice(0, -1)} ID.`));
      return;
    }
    try {
      setResource(await client.getReference(collection, resourceId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, collection, resourceId, routeIsCanonical]);

  useEffect(() => { void load(); }, [load]);

  if (error && !resource) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!resource) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div><p className="eyebrow">{resource.type}</p><h1>{titleFor(resource)}</h1><CanonicalId value={resource.id} /></div>
        <StatusBadge value="read-only" />
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <Card title="Canonical reference">
        <dl>
          <div><dt>Task</dt><dd><AppLink href={`/tasks/${resource.task_id}`}><CanonicalId value={resource.task_id} /></AppLink></dd></div>
          {resource.type === "plan" ? <div><dt>Steps</dt><dd>{resource.step_ids.length}</dd></div> : null}
          {resource.type === "step" ? <div><dt>Plan</dt><dd>{resource.plan_id ? <AppLink href={`/plans/${resource.plan_id}`}><CanonicalId value={resource.plan_id} /></AppLink> : "—"}</dd></div> : null}
        </dl>
      </Card>
      {resource.type === "plan" ? (
        <Card title="Plan steps">
          {resource.step_ids.length ? <ul className="reference-list">{resource.step_ids.map((stepId) => <li key={stepId}><AppLink href={`/steps/${stepId}`}><CanonicalId value={stepId} /></AppLink></li>)}</ul> : <EmptyState title="No steps" />}
        </Card>
      ) : null}
      {resource.type === "artifact" ? (
        <DegradedState
          title="No raw file operation exposed"
          detail="This canonical artifact resource currently contains identity and Task ownership only. Storage location, download, preview and mutation are intentionally not inferred from provider-private state."
        />
      ) : null}
      <div className="actions"><AppLink href="/files">Back to references</AppLink><button onClick={() => void load()}>Refresh</button></div>
    </div>
  );
}

function ReferenceTable({ items }: { items: CanonicalReference[] }) {
  if (!items.length) return <EmptyState title="No references" />;
  return (
    <div className="table-wrap"><table><thead><tr><th>Reference</th><th>Type</th><th>Task</th><th>Relation</th></tr></thead><tbody>
      {items.map((item) => (
        <tr key={item.id}>
          <td><AppLink href={pathFor(item)}><CanonicalId value={item.id} /></AppLink></td>
          <td><StatusBadge value={item.type} /></td>
          <td><AppLink href={`/tasks/${item.task_id}`}><CanonicalId value={item.task_id} /></AppLink></td>
          <td>{relationFor(item)}</td>
        </tr>
      ))}
    </tbody></table></div>
  );
}

function pathFor(item: CanonicalReference): string {
  if (item.type === "artifact") return `/artifacts/${item.id}`;
  if (item.type === "result") return `/results/${item.id}`;
  if (item.type === "plan") return `/plans/${item.id}`;
  return `/steps/${item.id}`;
}

function relationFor(item: CanonicalReference): string {
  if (item.type === "plan") return `${item.step_ids.length} step${item.step_ids.length === 1 ? "" : "s"}`;
  if (item.type === "step") return item.plan_id ?? "No plan";
  return "Task-owned";
}

function titleFor(item: CanonicalReference): string {
  return item.type === "artifact" ? "Artifact reference" : item.type === "result" ? "Result reference" : item.type === "plan" ? "Plan reference" : "Step reference";
}

function labelFor(collection: ReferenceCollection): string {
  return collection.charAt(0).toUpperCase() + collection.slice(1);
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}