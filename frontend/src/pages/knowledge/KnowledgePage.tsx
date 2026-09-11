import { useCallback, useEffect, useState } from "react";
import {
  type CanonicalKnowledgeSource,
  MemoryKnowledgeClient,
} from "../../api/memoryKnowledge";
import type { Page } from "../../api/types";
import { useCursorPagination } from "../../app/pagination";
import { AppLink } from "../../app/router";
import { PaginationControls } from "../../components/Pagination";
import { Card, CanonicalId, ErrorState, LoadingState } from "../../components/States";
import { blankToUndefined, parseJsonObject } from "../memoryKnowledge/input";
import { KnowledgeRegisterForm, type KnowledgeRegisterDraft } from "./forms";
import { KnowledgeSearchPanel } from "./KnowledgeSearchPanel";
import { KnowledgeSourceTable } from "./tables";

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
        <p>Durable source identity and explicit ingestion/re-index lifecycle. Provider-private vector, index and object-store identifiers are deliberately absent from this surface.</p>
      </header>

      <Card title="Knowledge source inventory">
        <label className="field"><span>Project ID filter (optional)</span><input value={projectId} onChange={(event) => setProjectId(event.target.value)} /></label>
        {error ? <ErrorState error={error} onRetry={() => void loadSources()} /> : null}
        {!sources && !error ? <LoadingState /> : null}
        {sources ? <KnowledgeSourceTable sources={sources.items} /> : null}
        {sources ? <PaginationControls page={sources} pageNumber={sourcePagination.pageNumber} hasPrevious={sourcePagination.hasPrevious} onPrevious={sourcePagination.previous} onRefresh={() => void loadSources()} onNext={() => sourcePagination.next(sources.next_cursor)} /> : null}
      </Card>

      <KnowledgeSearchPanel client={client} />

      <Card title="Register Knowledge source">
        <p>For a Project-scoped source, both target ref and Project ID are the canonical Project ID. For an unscoped source, target ref is the authenticated actor's canonical principal ref.</p>
        {actionError ? <ErrorState error={actionError} /> : null}
        <KnowledgeRegisterForm disabled={registering} onSubmit={registerSource} />
        {created ? <p role="status">Registered <AppLink href={`/knowledge/${encodeURIComponent(created.id)}`}><CanonicalId value={created.id} /></AppLink></p> : null}
      </Card>
    </div>
  );
}
