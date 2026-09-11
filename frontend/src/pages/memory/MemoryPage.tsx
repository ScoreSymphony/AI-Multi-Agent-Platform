import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type CanonicalMemoryEntry,
  MemoryKnowledgeClient,
  type MemoryScope,
} from "../../api/memoryKnowledge";
import type { Page } from "../../api/types";
import { useCursorPagination } from "../../app/pagination";
import { AppLink } from "../../app/router";
import { PaginationControls } from "../../components/Pagination";
import { Card, CanonicalId, ErrorState, LoadingState } from "../../components/States";
import { blankToUndefined, parseJsonObject, parseJsonValue } from "../memoryKnowledge/input";
import { MEMORY_SCOPES, MEMORY_TYPES } from "./constants";
import { MemoryCreateForm, type MemoryCreateDraft } from "./forms";
import { MemoryTable } from "./MemoryTable";
import {
  buildMemoryQueryKey,
  memoryTypeFilterValue,
  type MemoryTypeFilter,
} from "./query";

export function MemoryPage({ client }: { client: MemoryKnowledgeClient }) {
  const [scope, setScope] = useState<MemoryScope>("user");
  const [scopeId, setScopeId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [search, setSearch] = useState("");
  const [memoryType, setMemoryType] = useState<MemoryTypeFilter>("all");
  const [includeExpired, setIncludeExpired] = useState(false);
  const [includeSuperseded, setIncludeSuperseded] = useState(false);
  const [page, setPage] = useState<Page<CanonicalMemoryEntry> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [created, setCreated] = useState<CanonicalMemoryEntry | null>(null);
  const [creating, setCreating] = useState(false);

  const queryKey = useMemo(
    () => buildMemoryQueryKey({
      scope,
      scopeId,
      projectId,
      search,
      memoryType,
      includeExpired,
      includeSuperseded,
    }),
    [includeExpired, includeSuperseded, memoryType, projectId, scope, scopeId, search],
  );
  const pagination = useCursorPagination(`memory:${queryKey}`);

  const load = useCallback(async () => {
    try {
      const next = await client.listMemory({
        scope,
        scopeId: blankToUndefined(scopeId),
        projectId: blankToUndefined(projectId),
        memoryType: memoryTypeFilterValue(memoryType),
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
  }, [client, includeExpired, includeSuperseded, memoryType, pagination.cursor, projectId, scope, scopeId, search]);

  useEffect(() => {
    void load();
  }, [load]);

  async function createMemory(value: MemoryCreateDraft) {
    setCreating(true);
    try {
      if (!value.memoryType) {
        throw new Error("Memory Type is required for explicit Memory creation");
      }
      const createdMemory = await client.createMemory({
        scope: value.scope,
        scopeId: value.scopeId.trim(),
        origin: value.origin,
        memoryType: value.memoryType,
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
          Canonical scoped Memory content. Scope answers where / for whom an entry belongs; Memory
          Type answers what kind of Memory it represents. These dimensions are independent.
        </p>
      </header>

      <Card title="Scope and query">
        <div className="form-grid">
          <label className="field"><span>Scope</span><select value={scope} onChange={(event) => setScope(event.target.value as MemoryScope)}>{MEMORY_SCOPES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          <label className="field">
            <span>Memory Type</span>
            <select value={memoryType} onChange={(event) => setMemoryType(event.target.value as MemoryTypeFilter)}>
              <option value="all">all types</option>
              {MEMORY_TYPES.map((value) => <option key={value} value={value}>{value === "unclassified" ? "unclassified (legacy compatibility)" : value}</option>)}
            </select>
          </label>
          <label className="field"><span>Scope ID</span><input value={scopeId} onChange={(event) => setScopeId(event.target.value)} placeholder={scope === "user" ? "optional for your own user scope" : "canonical scope ID"} /></label>
          <label className="field"><span>Project ID (optional)</span><input value={projectId} onChange={(event) => setProjectId(event.target.value)} /></label>
          <label className="field"><span>Content search (optional)</span><input value={search} onChange={(event) => setSearch(event.target.value)} /></label>
          <label className="field"><span><input type="checkbox" checked={includeExpired} onChange={(event) => setIncludeExpired(event.target.checked)} /> Include expired</span></label>
          <label className="field"><span><input type="checkbox" checked={includeSuperseded} onChange={(event) => setIncludeSuperseded(event.target.checked)} /> Include superseded</span></label>
        </div>
        <p className="muted">Scope identifies where / for whom Memory belongs. Memory Type is the independent canonical semantic classification. “All types” omits the type filter.</p>
      </Card>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <Card title="Memory entries">
        {!page && !error ? <LoadingState /> : null}
        {page ? <MemoryTable entries={page.items} /> : null}
        {page ? <PaginationControls page={page} pageNumber={pagination.pageNumber} hasPrevious={pagination.hasPrevious} onPrevious={pagination.previous} onRefresh={() => void load()} onNext={() => pagination.next(page.next_cursor)} /> : null}
      </Card>

      <Card title="Create Memory explicitly">
        <p>Creation requires an explicit canonical scope ID, origin and Memory Type. For Project-scoped Memory use <code>workspace</code> with the canonical Project ID as the scope ID.</p>
        <p className="muted"><code>unclassified</code> is available only as an explicit legacy/import compatibility choice; normal interactive creation should select the semantic type that actually applies.</p>
        {actionError ? <ErrorState error={actionError} /> : null}
        <MemoryCreateForm disabled={creating} onSubmit={createMemory} />
        {created ? <p role="status">Created <AppLink href={`/memory/${encodeURIComponent(created.id)}`}><CanonicalId value={created.id} /></AppLink>{" · type "}<code>{created.memory_type}</code></p> : null}
      </Card>
    </div>
  );
}
