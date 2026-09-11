import type { CanonicalMemoryEntry } from "../../api/memoryKnowledge";
import { AppLink } from "../../app/router";
import { CanonicalId, EmptyState } from "../../components/States";
import { Detail, formatTimestamp } from "../memoryKnowledge/presentation";

export function MemoryTable({ entries }: { entries: CanonicalMemoryEntry[] }) {
  if (entries.length === 0) {
    return <EmptyState title="No Memory entries" detail="No authorized Memory entries match this scope and query." />;
  }
  return (
    <div className="table-wrap"><table><thead><tr><th>Memory</th><th>Scope</th><th>Type</th><th>Origin</th><th>Retention</th><th>Supersession</th></tr></thead><tbody>
      {entries.map((entry) => (
        <tr key={entry.id}>
          <td><AppLink href={`/memory/${encodeURIComponent(entry.id)}`}><CanonicalId value={entry.id} /></AppLink><br /><span className="muted">{formatTimestamp(entry.created_at)}</span></td>
          <td>{entry.scope}<br /><CanonicalId value={entry.scope_id} /></td>
          <td>{entry.memory_type}</td>
          <td>{entry.origin}</td>
          <td>{entry.retention}{entry.expires_at ? <><br /><span className="muted">{formatTimestamp(entry.expires_at)}</span></> : null}</td>
          <td>{entry.superseded_by_memory_id ? <>superseded by <CanonicalId value={entry.superseded_by_memory_id} /></> : entry.supersedes_memory_id ? <>supersedes <CanonicalId value={entry.supersedes_memory_id} /></> : "current"}</td>
        </tr>
      ))}
    </tbody></table></div>
  );
}

export function MemoryTypeDetail({ entry }: { entry: CanonicalMemoryEntry }) {
  return <Detail label="Memory Type">{entry.memory_type}</Detail>;
}
