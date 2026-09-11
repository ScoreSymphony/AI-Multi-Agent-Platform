import { useCallback, useEffect, useState } from "react";
import {
  type CanonicalKnowledgeSource,
  MemoryKnowledgeClient,
} from "../../api/memoryKnowledge";
import { Card, CanonicalId, ErrorState, LoadingState, StatusBadge } from "../../components/States";
import { Detail, formatTimestamp, JsonBlock } from "../memoryKnowledge/presentation";
import {
  KnowledgeIngestForm,
  KnowledgeReindexForm,
  KnowledgeUpdateForm,
} from "./forms";

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
        <h3>Metadata</h3><JsonBlock value={source.metadata} />
      </Card>

      <Card title="Update source metadata">
        <KnowledgeUpdateForm key={`${source.id}:${source.updated_at}`} source={source} disabled={busy} onSubmit={(title, metadata) => mutate(() => client.updateKnowledge(source.id, { title, metadata }))} />
      </Card>
      <Card title="Ingest source-backed content"><KnowledgeIngestForm disabled={busy} submitLabel="Ingest" onSubmit={(content, location) => mutate(() => client.ingestKnowledge(source.id, { content, location }))} /></Card>
      <Card title="Re-index with an explicit source revision"><KnowledgeReindexForm disabled={busy} onSubmit={(revision, content, location) => mutate(() => client.reindexKnowledge(source.id, { revision, content, location }))} /></Card>
      <Card title="Removal lifecycle">
        <p>Detach/delete uses the canonical tombstone semantics: active retrieval is removed while source identity remains available for historical citations.</p>
        <div className="button-row">
          <button disabled={busy || source.status === "removed"} onClick={() => { if (window.confirm("Detach this Knowledge source from active retrieval?")) void mutate(() => client.detachKnowledge(source.id)); }}>Detach</button>
          <button disabled={busy || source.status === "removed"} onClick={() => { if (window.confirm("Delete this Knowledge source using canonical tombstone semantics?")) void mutate(() => client.deleteKnowledge(source.id)); }}>Delete</button>
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
        </div>
      </Card>
    </div>
  );
}
