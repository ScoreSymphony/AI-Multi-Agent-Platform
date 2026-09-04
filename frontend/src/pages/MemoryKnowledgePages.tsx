import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  MemoryKnowledgeClient,
  type CanonicalKnowledgeResult,
  type CanonicalKnowledgeSource,
  type CanonicalMemoryEntry,
  type KnowledgeSearchMode,
  type MemoryOrigin,
  type MemoryRetention,
  type MemoryScope,
} from "../api/memoryKnowledge";
import type { JsonValue, Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  Card,
  CanonicalId,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const MEMORY_SCOPES: MemoryScope[] = [
  "user",
  "workspace",
  "agent",
  "task",
  "organization",
  "historical",
  "short_term",
];
const DURABLE_MEMORY_SCOPES: Exclude<MemoryScope, "short_term">[] = [
  "user",
  "workspace",
  "agent",
  "task",
  "organization",
  "historical",
];
const MEMORY_ORIGINS: MemoryOrigin[] = ["user-authored", "agent-derived", "imported"];
const MEMORY_RETENTIONS: MemoryRetention[] = [
  "ephemeral",
  "task_lifetime",
  "project_lifetime",
  "user_lifetime",
  "durable",
  "until",
];
const KNOWLEDGE_SEARCH_MODES: KnowledgeSearchMode[] = ["keyword", "semantic", "hybrid"];

export function MemoryPage({ client }: { client: MemoryKnowledgeClient }) {
  const [scope, setScope] = useState<MemoryScope>("user");
  const [scopeId, setScopeId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [search, setSearch] = useState("");
  const [includeExpired, setIncludeExpired] = useState(false);
  const [includeSuperseded, setIncludeSuperseded] = useState(false);
  const [page, setPage] = useState<Page<CanonicalMemoryEntry> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [created, setCreated] = useState<CanonicalMemoryEntry | null>(null);
  const [creating, setCreating] = useState(false);

  const queryKey = useMemo(
    () => [scope, scopeId.trim(), projectId.trim(), search.trim(), includeExpired, includeSuperseded].join("|"),
    [includeExpired, includeSuperseded, projectId, scope, scopeId, search],
  );
  const pagination = useCursorPagination(`memory:${queryKey}`);

  const load = useCallback(async () => {
    try {
      const next = await client.listMemory({
        scope,
        scopeId: blankToUndefined(scopeId),
        projectId: blankToUndefined(projectId),
        search: blankToUndefined(search),
        includeExpired,
        includeSuperseded,
        limit: 50,
        cursor: pagination.cursor,
      });
      setPage(next);
      setError(null);
    } catch (nextError) {
      setError(nextError);
      setPage(null);
    }
  }, [client, includeExpired, includeSuperseded, pagination.cursor, projectId, scope, scopeId, search]);

  useEffect(() => {
    void load();
  }, [load]);

  async function createMemory(value: MemoryCreateDraft) {
    setCreating(true);
    try {
      const createdMemory = await client.createMemory({
        scope: value.scope,
        scopeId: value.scopeId.trim(),
        origin: value.origin,
        value: parseJsonValue(value.valueJson, "Memory value"),
        retention: value.retention || undefined,
        expiresAt: blankToUndefined(value.expiresAt),
        projectId: blankToUndefined(value.projectId),
        classification: blankToUndefined(value.classification),
        metadata: parseJsonObject(value.metadataJson, "Memory metadata"),
      });
      setCreated(createdMemory);
      setActionError(null);
      await load();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Scoped durable context</p>
        <h1>Memory</h1>
        <p>
          Canonical scoped Memory content. Chat, Tasks and Events are not silently promoted here;
          durable entries are created or promoted explicitly and retain canonical provenance.
        </p>
      </header>

      <Card title="Scope and query">
        <div className="form-grid">
          <label className="field">
            <span>Scope</span>
            <select value={scope} onChange={(event) => setScope(event.target.value as MemoryScope)}>
              {MEMORY_SCOPES.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Scope ID</span>
            <input
              value={scopeId}
              onChange={(event) => setScopeId(event.target.value)}
              placeholder={scope === "user" ? "optional for your own user scope" : "canonical scope ID"}
            />
          </label>
          <label className="field">
            <span>Project ID (optional)</span>
            <input value={projectId} onChange={(event) => setProjectId(event.target.value)} />
          </label>
          <label className="field">
            <span>Content search (optional)</span>
            <input value={search} onChange={(event) => setSearch(event.target.value)} />
          </label>
          <label className="field">
            <span><input type="checkbox" checked={includeExpired} onChange={(event) => setIncludeExpired(event.target.checked)} /> Include expired</span>
          </label>
          <label className="field">
            <span><input type="checkbox" checked={includeSuperseded} onChange={(event) => setIncludeSuperseded(event.target.checked)} /> Include superseded</span>
          </label>
        </div>
      </Card>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <Card title="Memory entries">
        {!page && !error ? <LoadingState /> : null}
        {page ? <MemoryTable entries={page.items} /> : null}
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

      <Card title="Create Memory explicitly">
        <p>
          Creation requires an explicit canonical scope ID and origin. For Project-scoped Memory use
          <code>workspace</code> with the canonical Project ID as the scope ID.
        </p>
        {actionError ? <ErrorState error={actionError} /> : null}
        <MemoryCreateForm disabled={creating} onSubmit={createMemory} />
        {created ? (
          <p role="status">
            Created <AppLink href={`/memory/${encodeURIComponent(created.id)}`}><CanonicalId value={created.id} /></AppLink>
          </p>
        ) : null}
      </Card>
    </div>
  );
}

export function MemoryDetailPage({
  client,
  memoryId,
}: {
  client: MemoryKnowledgeClient;
  memoryId: string;
}) {
  const [entry, setEntry] = useState<CanonicalMemoryEntry | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [replacement, setReplacement] = useState<CanonicalMemoryEntry | null>(null);
  const [deleted, setDeleted] = useState(false);

  const load = useCallback(async () => {
    try {
      setEntry(await client.getMemory(memoryId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, memoryId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function updateMemory(valueJson: string, classification: string, metadataJson: string) {
    setBusy(true);
    try {
      const next = await client.updateMemory(memoryId, {
        value: parseJsonValue(valueJson, "Memory value"),
        classification: blankToNull(classification),
        metadata: parseJsonObject(metadataJson, "Memory metadata"),
      });
      setReplacement(next);
      setActionError(null);
      await load();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  }

  async function promoteMemory(scope: Exclude<MemoryScope, "short_term">, scopeId: string, projectId: string) {
    setBusy(true);
    try {
      const next = await client.promoteMemory(memoryId, {
        scope,
        scopeId: scopeId.trim(),
        projectId: blankToUndefined(projectId),
      });
      setReplacement(next);
      setActionError(null);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  }

  async function expireMemory() {
    if (!entry) return;
    setBusy(true);
    try {
      await client.expireMemory(memoryId, {
        scope: entry.scope,
        scopeId: entry.scope_id,
        projectId: entry.project_id,
      });
      setActionError(null);
      await load();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  }

  async function deleteMemory() {
    if (!window.confirm("Delete this canonical Memory entry?")) return;
    setBusy(true);
    try {
      await client.deleteMemory(memoryId);
      setDeleted(true);
      setActionError(null);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!entry) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical Memory entry</p>
        <h1>Memory detail</h1>
        <p><CanonicalId value={entry.id} /></p>
      </header>

      {actionError ? <ErrorState error={actionError} /> : null}
      {deleted ? <p role="status">The delete command completed for this Memory entry.</p> : null}
      {replacement ? (
        <p role="status">
          New canonical entry: <AppLink href={`/memory/${encodeURIComponent(replacement.id)}`}><CanonicalId value={replacement.id} /></AppLink>
        </p>
      ) : null}

      <Card title="Scope, provenance and retention">
        <dl className="detail-grid">
          <Detail label="Scope">{entry.scope}</Detail>
          <Detail label="Scope ID"><CanonicalId value={entry.scope_id} /></Detail>
          <Detail label="Project">{entry.project_id ? <CanonicalId value={entry.project_id} /> : "—"}</Detail>
          <Detail label="Owner"><code>{entry.owner_ref}</code></Detail>
          <Detail label="Origin">{entry.origin}</Detail>
          <Detail label="Retention">{entry.retention}</Detail>
          <Detail label="Expires">{entry.expires_at ? formatTimestamp(entry.expires_at) : "—"}</Detail>
          <Detail label="Classification">{entry.classification ?? "—"}</Detail>
          <Detail label="Created">{formatTimestamp(entry.created_at)}</Detail>
          <Detail label="Created by"><code>{entry.created_by}</code></Detail>
        </dl>
        <h3>Value</h3>
        <JsonBlock value={entry.value} />
        <h3>Provenance</h3>
        <JsonBlock value={entry.provenance} />
        <h3>Metadata</h3>
        <JsonBlock value={entry.metadata} />
        <dl className="detail-grid">
          <Detail label="Supersedes">{entry.supersedes_memory_id ? <AppLink href={`/memory/${encodeURIComponent(entry.supersedes_memory_id)}`}><CanonicalId value={entry.supersedes_memory_id} /></AppLink> : "—"}</Detail>
          <Detail label="Superseded by">{entry.superseded_by_memory_id ? <AppLink href={`/memory/${encodeURIComponent(entry.superseded_by_memory_id)}`}><CanonicalId value={entry.superseded_by_memory_id} /></AppLink> : "—"}</Detail>
        </dl>
      </Card>

      <Card title="Supersede with an explicit update">
        <p>Updates create a new canonical Memory ID rather than rewriting this entry in place.</p>
        <MemoryUpdateForm entry={entry} disabled={busy} onSubmit={updateMemory} />
      </Card>

      {entry.scope === "short_term" ? (
        <Card title="Promote short-term Memory">
          <p>Promotion is explicit and preserves a provenance reference to this short-term entry.</p>
          <MemoryPromoteForm disabled={busy} onSubmit={promoteMemory} />
        </Card>
      ) : null}

      <Card title="Lifecycle">
        <div className="button-row">
          <button disabled={busy || entry.expires_at === null} onClick={() => void expireMemory()}>
            Expire when due
          </button>
          <button disabled={busy || deleted} onClick={() => void deleteMemory()}>Delete Memory</button>
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
        </div>
        {entry.expires_at === null ? <p className="muted">This entry has no expiration timestamp, so the exact expire command is not applicable.</p> : null}
      </Card>
    </div>
  );
}

export function KnowledgePage({ client }: { client: MemoryKnowledgeClient }) {
  const [projectId, setProjectId] = useState("");
  const [sources, setSources] = useState<Page<CanonicalKnowledgeSource> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [registering, setRegistering] = useState(false);
  const [created, setCreated] = useState<CanonicalKnowledgeSource | null>(null);
  const sourcePagination = useCursorPagination(`knowledge:${projectId.trim()}`);

  const loadSources = useCallback(async () => {
    try {
      setSources(await client.listKnowledge({
        projectId: blankToUndefined(projectId),
        limit: 50,
        cursor: sourcePagination.cursor,
      }));
      setError(null);
    } catch (nextError) {
      setError(nextError);
      setSources(null);
    }
  }, [client, projectId, sourcePagination.cursor]);

  useEffect(() => {
    void loadSources();
  }, [loadSources]);

  async function registerSource(draft: KnowledgeRegisterDraft) {
    setRegistering(true);
    try {
      const next = await client.registerKnowledge({
        targetRef: draft.targetRef.trim(),
        title: draft.title.trim(),
        revision: blankToUndefined(draft.revision),
        projectId: blankToUndefined(draft.projectId),
        metadata: parseJsonObject(draft.metadataJson, "Knowledge metadata"),
      });
      setCreated(next);
      setActionError(null);
      await loadSources();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setRegistering(false);
    }
  }

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Source-backed canonical retrieval</p>
        <h1>Knowledge</h1>
        <p>
          Durable source identity and explicit ingestion/re-index lifecycle. Provider-private vector,
          index and object-store identifiers are deliberately absent from this surface.
        </p>
      </header>

      <Card title="Knowledge source inventory">
        <label className="field">
          <span>Project ID filter (optional)</span>
          <input value={projectId} onChange={(event) => setProjectId(event.target.value)} />
        </label>
        {error ? <ErrorState error={error} onRetry={() => void loadSources()} /> : null}
        {!sources && !error ? <LoadingState /> : null}
        {sources ? <KnowledgeSourceTable sources={sources.items} /> : null}
        {sources ? (
          <PaginationControls
            page={sources}
            pageNumber={sourcePagination.pageNumber}
            hasPrevious={sourcePagination.hasPrevious}
            onPrevious={sourcePagination.previous}
            onRefresh={() => void loadSources()}
            onNext={() => sourcePagination.next(sources.next_cursor)}
          />
        ) : null}
      </Card>

      <KnowledgeSearchPanel client={client} />

      <Card title="Register Knowledge source">
        <p>
          For a Project-scoped source, both target ref and Project ID are the canonical Project ID.
          For an unscoped source, target ref is the authenticated actor's canonical principal ref.
        </p>
        {actionError ? <ErrorState error={actionError} /> : null}
        <KnowledgeRegisterForm disabled={registering} onSubmit={registerSource} />
        {created ? (
          <p role="status">
            Registered <AppLink href={`/knowledge/${encodeURIComponent(created.id)}`}><CanonicalId value={created.id} /></AppLink>
          </p>
        ) : null}
      </Card>
    </div>
  );
}

export function KnowledgeDetailPage({
  client,
  sourceId,
}: {
  client: MemoryKnowledgeClient;
  sourceId: string;
}) {
  const [source, setSource] = useState<CanonicalKnowledgeSource | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setSource(await client.getKnowledge(sourceId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, sourceId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function mutate(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      setActionError(null);
      await load();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!source) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical Knowledge source</p>
        <h1>{source.title}</h1>
        <p><CanonicalId value={source.id} /></p>
      </header>

      {actionError ? <ErrorState error={actionError} /> : null}

      <Card title="Source identity and status">
        <dl className="detail-grid">
          <Detail label="Status"><StatusBadge value={source.status} /></Detail>
          <Detail label="Revision">{source.revision}</Detail>
          <Detail label="Project">{source.project_id ? <CanonicalId value={source.project_id} /> : "—"}</Detail>
          <Detail label="Owner"><code>{source.owner_ref}</code></Detail>
          <Detail label="Created by"><code>{source.created_by}</code></Detail>
          <Detail label="Created">{formatTimestamp(source.created_at)}</Detail>
          <Detail label="Updated">{formatTimestamp(source.updated_at)}</Detail>
          <Detail label="Content checksum">{source.content_checksum ? <code>{source.content_checksum}</code> : "—"}</Detail>
        </dl>
        <h3>Metadata</h3>
        <JsonBlock value={source.metadata} />
      </Card>

      <Card title="Update source metadata">
        <KnowledgeUpdateForm
          key={`${source.id}:${source.updated_at}`}
          source={source}
          disabled={busy}
          onSubmit={(title, metadata) => mutate(() => client.updateKnowledge(source.id, { title, metadata }))}
        />
      </Card>

      <Card title="Ingest source-backed content">
        <KnowledgeIngestForm
          disabled={busy}
          submitLabel="Ingest"
          onSubmit={(content, location) => mutate(() => client.ingestKnowledge(source.id, { content, location }))}
        />
      </Card>

      <Card title="Re-index with an explicit source revision">
        <KnowledgeReindexForm
          disabled={busy}
          onSubmit={(revision, content, location) => mutate(() => client.reindexKnowledge(source.id, { revision, content, location }))}
        />
      </Card>

      <Card title="Removal lifecycle">
        <p>
          Detach/delete uses the canonical tombstone semantics: active retrieval is removed while
          source identity remains available for historical citations.
        </p>
        <div className="button-row">
          <button disabled={busy || source.status === "removed"} onClick={() => {
            if (window.confirm("Detach this Knowledge source from active retrieval?")) {
              void mutate(() => client.detachKnowledge(source.id));
            }
          }}>Detach</button>
          <button disabled={busy || source.status === "removed"} onClick={() => {
            if (window.confirm("Delete this Knowledge source using canonical tombstone semantics?")) {
              void mutate(() => client.deleteKnowledge(source.id));
            }
          }}>Delete</button>
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
        </div>
      </Card>
    </div>
  );
}

function KnowledgeSearchPanel({ client }: { client: MemoryKnowledgeClient }) {
  const [draft, setDraft] = useState<KnowledgeSearchDraft>({
    query: "",
    mode: "keyword",
    sourceId: "",
    projectId: "",
  });
  const [request, setRequest] = useState<KnowledgeSearchDraft | null>(null);
  const [page, setPage] = useState<Page<CanonicalKnowledgeResult> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const queryKey = request
    ? [request.query, request.mode, request.sourceId, request.projectId].join("|")
    : "idle";
  const pagination = useCursorPagination(`knowledge-results:${queryKey}`);

  const load = useCallback(async () => {
    if (!request) return;
    try {
      setPage(await client.searchKnowledge({
        query: request.query,
        mode: request.mode,
        sourceId: blankToUndefined(request.sourceId),
        projectId: blankToUndefined(request.projectId),
        limit: 25,
        cursor: pagination.cursor,
      }));
      setError(null);
    } catch (nextError) {
      setError(nextError);
      setPage(null);
    }
  }, [client, pagination.cursor, request]);

  useEffect(() => {
    void load();
  }, [load]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const query = draft.query.trim();
    if (!query) {
      setError(new Error("Knowledge query is required"));
      return;
    }
    setRequest({ ...draft, query });
  }

  return (
    <Card title="Retrieve Knowledge with canonical citations">
      <form className="form-grid" onSubmit={submit}>
        <label className="field field-wide"><span>Query</span><input required value={draft.query} onChange={(event) => setDraft((current) => ({ ...current, query: event.target.value }))} /></label>
        <label className="field"><span>Mode</span><select value={draft.mode} onChange={(event) => setDraft((current) => ({ ...current, mode: event.target.value as KnowledgeSearchMode }))}>{KNOWLEDGE_SEARCH_MODES.map((mode) => <option key={mode} value={mode}>{mode}</option>)}</select></label>
        <label className="field"><span>Source ID (optional)</span><input value={draft.sourceId} onChange={(event) => setDraft((current) => ({ ...current, sourceId: event.target.value }))} /></label>
        <label className="field"><span>Project ID (optional)</span><input value={draft.projectId} onChange={(event) => setDraft((current) => ({ ...current, projectId: event.target.value }))} /></label>
        <button type="submit">Search Knowledge</button>
      </form>
      <p className="muted">Retrieval rows are query-scoped projections, not durable canonical result resources.</p>
      {error ? <ErrorState error={error} onRetry={request ? () => void load() : undefined} /> : null}
      {request && !page && !error ? <LoadingState /> : null}
      {page ? <KnowledgeResultTable results={page.items} /> : null}
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

interface MemoryCreateDraft {
  scope: MemoryScope;
  scopeId: string;
  origin: MemoryOrigin;
  valueJson: string;
  retention: MemoryRetention | "";
  expiresAt: string;
  projectId: string;
  classification: string;
  metadataJson: string;
}

function MemoryCreateForm({ disabled, onSubmit }: { disabled: boolean; onSubmit: (draft: MemoryCreateDraft) => Promise<void> }) {
  const [draft, setDraft] = useState<MemoryCreateDraft>({
    scope: "user",
    scopeId: "",
    origin: "user-authored",
    valueJson: "{}",
    retention: "",
    expiresAt: "",
    projectId: "",
    classification: "",
    metadataJson: "{}",
  });
  const set = (key: keyof MemoryCreateDraft, value: string) => setDraft((current) => ({ ...current, [key]: value }));
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(draft);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Scope</span><select value={draft.scope} onChange={(event) => set("scope", event.target.value)}>{MEMORY_SCOPES.map((scope) => <option key={scope} value={scope}>{scope}</option>)}</select></label>
      <label className="field"><span>Scope ID</span><input required value={draft.scopeId} onChange={(event) => set("scopeId", event.target.value)} /></label>
      <label className="field"><span>Origin</span><select value={draft.origin} onChange={(event) => set("origin", event.target.value)}>{MEMORY_ORIGINS.map((origin) => <option key={origin} value={origin}>{origin}</option>)}</select></label>
      <label className="field"><span>Retention (optional)</span><select value={draft.retention} onChange={(event) => set("retention", event.target.value)}><option value="">server default</option>{MEMORY_RETENTIONS.map((retention) => <option key={retention} value={retention}>{retention}</option>)}</select></label>
      <label className="field"><span>Expires at ISO timestamp (optional)</span><input value={draft.expiresAt} onChange={(event) => set("expiresAt", event.target.value)} /></label>
      <label className="field"><span>Project ID (optional)</span><input value={draft.projectId} onChange={(event) => set("projectId", event.target.value)} /></label>
      <label className="field"><span>Classification (optional)</span><input value={draft.classification} onChange={(event) => set("classification", event.target.value)} /></label>
      <label className="field field-wide"><span>Value JSON</span><textarea required rows={6} value={draft.valueJson} onChange={(event) => set("valueJson", event.target.value)} /></label>
      <label className="field field-wide"><span>Metadata JSON object</span><textarea rows={5} value={draft.metadataJson} onChange={(event) => set("metadataJson", event.target.value)} /></label>
      <button disabled={disabled} type="submit">Create Memory</button>
    </form>
  );
}

function MemoryUpdateForm({ entry, disabled, onSubmit }: { entry: CanonicalMemoryEntry; disabled: boolean; onSubmit: (valueJson: string, classification: string, metadataJson: string) => Promise<void> }) {
  const [valueJson, setValueJson] = useState(JSON.stringify(entry.value, null, 2));
  const [classification, setClassification] = useState(entry.classification ?? "");
  const [metadataJson, setMetadataJson] = useState(JSON.stringify(entry.metadata, null, 2));
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(valueJson, classification, metadataJson);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field field-wide"><span>Replacement value JSON</span><textarea rows={6} required value={valueJson} onChange={(event) => setValueJson(event.target.value)} /></label>
      <label className="field"><span>Classification</span><input value={classification} onChange={(event) => setClassification(event.target.value)} /></label>
      <label className="field field-wide"><span>Metadata JSON object</span><textarea rows={5} value={metadataJson} onChange={(event) => setMetadataJson(event.target.value)} /></label>
      <button disabled={disabled} type="submit">Create superseding Memory</button>
    </form>
  );
}

function MemoryPromoteForm({ disabled, onSubmit }: { disabled: boolean; onSubmit: (scope: Exclude<MemoryScope, "short_term">, scopeId: string, projectId: string) => Promise<void> }) {
  const [scope, setScope] = useState<Exclude<MemoryScope, "short_term">>("user");
  const [scopeId, setScopeId] = useState("");
  const [projectId, setProjectId] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(scope, scopeId, projectId);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Target scope</span><select value={scope} onChange={(event) => setScope(event.target.value as Exclude<MemoryScope, "short_term">)}>{DURABLE_MEMORY_SCOPES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
      <label className="field"><span>Target scope ID</span><input required value={scopeId} onChange={(event) => setScopeId(event.target.value)} /></label>
      <label className="field"><span>Project ID (optional)</span><input value={projectId} onChange={(event) => setProjectId(event.target.value)} /></label>
      <button disabled={disabled} type="submit">Promote Memory</button>
    </form>
  );
}

interface KnowledgeRegisterDraft {
  targetRef: string;
  title: string;
  revision: string;
  projectId: string;
  metadataJson: string;
}

function KnowledgeRegisterForm({ disabled, onSubmit }: { disabled: boolean; onSubmit: (draft: KnowledgeRegisterDraft) => Promise<void> }) {
  const [draft, setDraft] = useState<KnowledgeRegisterDraft>({ targetRef: "", title: "", revision: "1", projectId: "", metadataJson: "{}" });
  const set = (key: keyof KnowledgeRegisterDraft, value: string) => setDraft((current) => ({ ...current, [key]: value }));
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(draft);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Target ref</span><input required value={draft.targetRef} onChange={(event) => set("targetRef", event.target.value)} /></label>
      <label className="field"><span>Title</span><input required value={draft.title} onChange={(event) => set("title", event.target.value)} /></label>
      <label className="field"><span>Revision</span><input value={draft.revision} onChange={(event) => set("revision", event.target.value)} /></label>
      <label className="field"><span>Project ID (optional)</span><input value={draft.projectId} onChange={(event) => set("projectId", event.target.value)} /></label>
      <label className="field field-wide"><span>Metadata JSON object</span><textarea rows={5} value={draft.metadataJson} onChange={(event) => set("metadataJson", event.target.value)} /></label>
      <button disabled={disabled} type="submit">Register source</button>
    </form>
  );
}

function KnowledgeUpdateForm({ source, disabled, onSubmit }: { source: CanonicalKnowledgeSource; disabled: boolean; onSubmit: (title: string, metadata: Record<string, JsonValue>) => Promise<void> }) {
  const [title, setTitle] = useState(source.title);
  const [metadataJson, setMetadataJson] = useState(JSON.stringify(source.metadata, null, 2));
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(title.trim(), parseJsonObject(metadataJson, "Knowledge metadata"));
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Title</span><input required value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <label className="field field-wide"><span>Metadata JSON object</span><textarea rows={5} value={metadataJson} onChange={(event) => setMetadataJson(event.target.value)} /></label>
      <button disabled={disabled} type="submit">Update metadata</button>
    </form>
  );
}

function KnowledgeIngestForm({ disabled, submitLabel, onSubmit }: { disabled: boolean; submitLabel: string; onSubmit: (content: string, location: string) => Promise<void> }) {
  const [content, setContent] = useState("");
  const [location, setLocation] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(content, location);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Canonical source location</span><input required value={location} onChange={(event) => setLocation(event.target.value)} /></label>
      <label className="field field-wide"><span>Content</span><textarea rows={8} required value={content} onChange={(event) => setContent(event.target.value)} /></label>
      <button disabled={disabled} type="submit">{submitLabel}</button>
    </form>
  );
}

function KnowledgeReindexForm({ disabled, onSubmit }: { disabled: boolean; onSubmit: (revision: string, content: string, location: string) => Promise<void> }) {
  const [revision, setRevision] = useState("");
  const [content, setContent] = useState("");
  const [location, setLocation] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(revision, content, location);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>New source revision</span><input required value={revision} onChange={(event) => setRevision(event.target.value)} /></label>
      <label className="field"><span>Canonical source location</span><input required value={location} onChange={(event) => setLocation(event.target.value)} /></label>
      <label className="field field-wide"><span>Content</span><textarea rows={8} required value={content} onChange={(event) => setContent(event.target.value)} /></label>
      <button disabled={disabled} type="submit">Re-index source</button>
    </form>
  );
}

interface KnowledgeSearchDraft {
  query: string;
  mode: KnowledgeSearchMode;
  sourceId: string;
  projectId: string;
}

function MemoryTable({ entries }: { entries: CanonicalMemoryEntry[] }) {
  if (entries.length === 0) return <EmptyState title="No Memory entries" detail="No authorized Memory entries match this scope and query." />;
  return (
    <div className="table-wrap"><table><thead><tr><th>Memory</th><th>Scope</th><th>Origin</th><th>Retention</th><th>Supersession</th></tr></thead><tbody>
      {entries.map((entry) => (
        <tr key={entry.id}>
          <td><AppLink href={`/memory/${encodeURIComponent(entry.id)}`}><CanonicalId value={entry.id} /></AppLink><br /><span className="muted">{formatTimestamp(entry.created_at)}</span></td>
          <td>{entry.scope}<br /><CanonicalId value={entry.scope_id} /></td>
          <td>{entry.origin}</td>
          <td>{entry.retention}{entry.expires_at ? <><br /><span className="muted">{formatTimestamp(entry.expires_at)}</span></> : null}</td>
          <td>{entry.superseded_by_memory_id ? <>superseded by <CanonicalId value={entry.superseded_by_memory_id} /></> : entry.supersedes_memory_id ? <>supersedes <CanonicalId value={entry.supersedes_memory_id} /></> : "current"}</td>
        </tr>
      ))}
    </tbody></table></div>
  );
}

function KnowledgeSourceTable({ sources }: { sources: CanonicalKnowledgeSource[] }) {
  if (sources.length === 0) return <EmptyState title="No Knowledge sources" detail="No authorized Knowledge sources are registered for this scope." />;
  return (
    <div className="table-wrap"><table><thead><tr><th>Source</th><th>Revision</th><th>Status</th><th>Project</th><th>Updated</th></tr></thead><tbody>
      {sources.map((source) => (
        <tr key={source.id}>
          <td><AppLink href={`/knowledge/${encodeURIComponent(source.id)}`}>{source.title}</AppLink><br /><CanonicalId value={source.id} /></td>
          <td>{source.revision}</td>
          <td><StatusBadge value={source.status} /></td>
          <td>{source.project_id ? <CanonicalId value={source.project_id} /> : "—"}</td>
          <td>{formatTimestamp(source.updated_at)}</td>
        </tr>
      ))}
    </tbody></table></div>
  );
}

function KnowledgeResultTable({ results }: { results: CanonicalKnowledgeResult[] }) {
  if (results.length === 0) return <EmptyState title="No Knowledge matches" detail="No authorized source-backed results matched this query." />;
  return (
    <div className="table-wrap"><table><thead><tr><th>Source / revision</th><th>Content</th><th>Location</th><th>Score</th><th>Citation</th></tr></thead><tbody>
      {results.map((result) => (
        <tr key={`${result.source_id}:${result.revision}:${result.id}`}>
          <td><AppLink href={`/knowledge/${encodeURIComponent(result.source_id)}`}><CanonicalId value={result.source_id} /></AppLink><br />rev {result.revision}</td>
          <td>{result.content}</td>
          <td><code>{result.location}</code></td>
          <td>{result.score ?? "—"}</td>
          <td><code>{result.citation.kind}:{result.citation.ref}</code>{result.citation.revision ? <> · rev {result.citation.revision}</> : null}</td>
        </tr>
      ))}
    </tbody></table></div>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return <><dt>{label}</dt><dd>{children}</dd></>;
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="code-block"><code>{JSON.stringify(value, null, 2)}</code></pre>;
}

function parseJsonValue(value: string, label: string): JsonValue {
  try {
    return JSON.parse(value) as JsonValue;
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
}

function parseJsonObject(value: string, label: string): Record<string, JsonValue> {
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

function blankToUndefined(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function blankToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
