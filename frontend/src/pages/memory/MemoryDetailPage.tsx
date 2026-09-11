import { useCallback, useEffect, useState } from "react";
import {
  type CanonicalMemoryEntry,
  MemoryKnowledgeClient,
  type MemoryScope,
} from "../../api/memoryKnowledge";
import { AppLink } from "../../app/router";
import { Card, CanonicalId, ErrorState, LoadingState } from "../../components/States";
import { blankToNull, blankToUndefined, parseJsonObject, parseJsonValue } from "../memoryKnowledge/input";
import { Detail, formatTimestamp, JsonBlock } from "../memoryKnowledge/presentation";
import { MemoryPromoteForm, MemoryUpdateForm } from "./forms";
import { MemoryTypeDetail } from "./MemoryTable";

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
      {replacement ? <p role="status">New canonical entry: <AppLink href={`/memory/${encodeURIComponent(replacement.id)}`}><CanonicalId value={replacement.id} /></AppLink>{" · type "}<code>{replacement.memory_type}</code></p> : null}

      <Card title="Scope, type, provenance and retention">
        <p className="muted">Scope identifies where / for whom this Memory belongs; Memory Type independently identifies what kind of Memory it represents.</p>
        <dl className="detail-grid">
          <Detail label="Scope">{entry.scope}</Detail>
          <MemoryTypeDetail entry={entry} />
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
        <h3>Value</h3><JsonBlock value={entry.value} />
        <h3>Provenance</h3><JsonBlock value={entry.provenance} />
        <h3>Metadata</h3><JsonBlock value={entry.metadata} />
        <dl className="detail-grid">
          <Detail label="Supersedes">{entry.supersedes_memory_id ? <AppLink href={`/memory/${encodeURIComponent(entry.supersedes_memory_id)}`}><CanonicalId value={entry.supersedes_memory_id} /></AppLink> : "—"}</Detail>
          <Detail label="Superseded by">{entry.superseded_by_memory_id ? <AppLink href={`/memory/${encodeURIComponent(entry.superseded_by_memory_id)}`}><CanonicalId value={entry.superseded_by_memory_id} /></AppLink> : "—"}</Detail>
        </dl>
      </Card>

      <Card title="Supersede with an explicit update">
        <p>Updates create a new canonical Memory ID rather than rewriting this entry in place. They preserve Memory Type <code>{entry.memory_type}</code>; semantic reclassification requires a new derived Memory with provenance to its source evidence.</p>
        <MemoryUpdateForm entry={entry} disabled={busy} onSubmit={updateMemory} />
      </Card>

      {entry.scope === "short_term" ? <Card title="Promote short-term Memory"><p>Promotion is explicit, preserves Memory Type <code>{entry.memory_type}</code>, and keeps a provenance reference to this short-term entry.</p><MemoryPromoteForm disabled={busy} onSubmit={promoteMemory} /></Card> : null}

      <Card title="Lifecycle">
        <div className="button-row">
          <button disabled={busy || entry.expires_at === null} onClick={() => void expireMemory()}>Expire when due</button>
          <button disabled={busy || deleted} onClick={() => void deleteMemory()}>Delete Memory</button>
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
        </div>
        {entry.expires_at === null ? <p className="muted">This entry has no expiration timestamp, so the exact expire command is not applicable.</p> : null}
      </Card>
    </div>
  );
}
