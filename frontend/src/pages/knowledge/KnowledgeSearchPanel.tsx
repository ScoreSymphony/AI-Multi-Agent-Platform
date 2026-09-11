import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  type CanonicalKnowledgeResult,
  type KnowledgeSearchMode,
  MemoryKnowledgeClient,
} from "../../api/memoryKnowledge";
import type { Page } from "../../api/types";
import { useCursorPagination } from "../../app/pagination";
import { PaginationControls } from "../../components/Pagination";
import { Card, ErrorState, LoadingState } from "../../components/States";
import { blankToUndefined } from "../memoryKnowledge/input";
import { KNOWLEDGE_SEARCH_MODES } from "./constants";
import { KnowledgeResultTable } from "./tables";

interface KnowledgeSearchDraft {
  query: string;
  mode: KnowledgeSearchMode;
  sourceId: string;
  projectId: string;
}

export function KnowledgeSearchPanel({ client }: { client: MemoryKnowledgeClient }) {
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
      {page ? <PaginationControls page={page} pageNumber={pagination.pageNumber} hasPrevious={pagination.hasPrevious} onPrevious={pagination.previous} onRefresh={() => void load()} onNext={() => pagination.next(page.next_cursor)} /> : null}
    </Card>
  );
}
