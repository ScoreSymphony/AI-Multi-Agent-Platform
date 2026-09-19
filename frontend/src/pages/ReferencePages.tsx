import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { ControlPlaneClient } from "../api/client";
import type { CanonicalFile } from "../api/files";
import { FilesClient } from "../api/files";
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

export function ReferencesPage({ client, files }: { client: ControlPlaneClient; files: FilesClient }) {
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
        <p className="eyebrow">Canonical data references</p>
        <h1>Files & artifacts</h1>
        <p>Canonical File metadata plus Artifact, Result, Plan and Step references exposed by the versioned Control Plane.</p>
      </header>

      <DegradedState
        title="File bytes remain outside this metadata surface"
        detail="The canonical /files collection exposes authorized File metadata, scope, checksums and Artifact relationships. Raw bytes, provider-private storage paths and storage backend identities are intentionally not inferred or fetched through a private route."
      />

      <FileInventory client={files} />

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

function FileInventory({ client }: { client: FilesClient }) {
  const [page, setPage] = useState<Page<CanonicalFile> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [projectInput, setProjectInput] = useState("");
  const [projectFilter, setProjectFilter] = useState("");
  const pagination = useCursorPagination(`files:${projectFilter}`);

  const load = useCallback(async () => {
    try {
      const next = await client.listFiles({
        limit: 100,
        cursor: pagination.cursor,
        filters: projectFilter ? { project_id: projectFilter } : undefined,
      });
      setPage(next);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, pagination.cursor, projectFilter]);

  useEffect(() => {
    setPage(null);
    void load();
  }, [load]);

  const applyProjectFilter = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalized = projectInput.trim();
    if (normalized !== projectFilter) {
      setProjectFilter(normalized);
      return;
    }
    if (pagination.cursor) {
      pagination.reset();
      return;
    }
    void load();
  };

  const clearProjectFilter = () => {
    setProjectInput("");
    if (projectFilter) {
      setProjectFilter("");
      return;
    }
    if (pagination.cursor) {
      pagination.reset();
      return;
    }
    void load();
  };

  return (
    <Card title="Files">
      <form className="filter-row" onSubmit={applyProjectFilter}>
        <label>
          Project ID
          <input
            value={projectInput}
            onChange={(event) => setProjectInput(event.target.value)}
            placeholder="project_…"
          />
        </label>
        <button type="submit">Filter</button>
        <button type="button" onClick={clearProjectFilter}>Clear</button>
      </form>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {!page ? <LoadingState label="Loading File metadata…" /> : <FileTable items={page.items} />}
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
    </Card>
  );
}

function FileTable({ items }: { items: CanonicalFile[] }) {
  if (!items.length) return <EmptyState title="No Files" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>File</th><th>State</th><th>Project</th><th>Content type</th><th>Size</th><th>Artifacts</th></tr></thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td><AppLink href={`/files/${item.id}`}><CanonicalId value={item.id} /></AppLink></td>
              <td><StatusBadge value={item.state} /></td>
              <td>{item.project_id ? <AppLink href={`/projects/${item.project_id}`}><CanonicalId value={item.project_id} /></AppLink> : "—"}</td>
              <td>{item.content_type ?? "—"}</td>
              <td>{formatBytes(item.size_bytes)}</td>
              <td>{item.artifact_ids.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FileDetailPage({ client, fileId }: { client: FilesClient; fileId: string }) {
  const [file, setFile] = useState<CanonicalFile | null>(null);
  const [error, setError] = useState<unknown>(null);
  const routeIsCanonical = useMemo(
    () => isCanonicalId(fileId) && fileId.startsWith("file_"),
    [fileId],
  );

  const load = useCallback(async () => {
    if (!routeIsCanonical) {
      setError(new Error("This route does not contain a canonical File ID."));
      return;
    }
    try {
      setFile(await client.getFile(fileId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, fileId, routeIsCanonical]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !file) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!file) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div><p className="eyebrow">File</p><h1>File metadata</h1><CanonicalId value={file.id} /></div>
        <StatusBadge value={file.state} />
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <Card title="Canonical File">
        <dl>
          <div><dt>Project</dt><dd>{file.project_id ? <AppLink href={`/projects/${file.project_id}`}><CanonicalId value={file.project_id} /></AppLink> : "—"}</dd></div>
          <div><dt>Owner</dt><dd><code>{file.owner_ref}</code></dd></div>
          <div><dt>Created by</dt><dd><code>{file.created_by}</code></dd></div>
          <div><dt>Created</dt><dd>{file.created_at}</dd></div>
          <div><dt>Content type</dt><dd>{file.content_type ?? "—"}</dd></div>
          <div><dt>Size</dt><dd>{formatBytes(file.size_bytes)}</dd></div>
          <div><dt>SHA-256</dt><dd><code>{file.sha256}</code></dd></div>
        </dl>
      </Card>
      <Card title="Artifact relationships">
        {file.artifact_ids.length ? (
          <ul className="reference-list">
            {file.artifact_ids.map((artifactId) => (
              <li key={artifactId}><AppLink href={`/artifacts/${artifactId}`}><CanonicalId value={artifactId} /></AppLink></li>
            ))}
          </ul>
        ) : <EmptyState title="No linked Artifacts" />}
      </Card>
      <Card title="Authorized metadata">
        <pre className="code-block">{JSON.stringify(file.metadata, null, 2)}</pre>
      </Card>
      <DegradedState
        title="Raw File bytes are not exposed here"
        detail="This page intentionally consumes only the canonical File metadata projection. It does not derive filesystem paths, storage-provider identities or a private download route."
      />
      <div className="actions"><AppLink href="/files">Back to Files & artifacts</AppLink><button onClick={() => void load()}>Refresh</button></div>
    </div>
  );
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GiB`;
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