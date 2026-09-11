import type {
  CanonicalKnowledgeResult,
  CanonicalKnowledgeSource,
} from "../../api/memoryKnowledge";
import { AppLink } from "../../app/router";
import { CanonicalId, EmptyState, StatusBadge } from "../../components/States";
import { formatTimestamp } from "../memoryKnowledge/presentation";

export function KnowledgeSourceTable({ sources }: { sources: CanonicalKnowledgeSource[] }) {
  if (sources.length === 0) {
    return <EmptyState title="No Knowledge sources" detail="No authorized Knowledge sources are registered for this scope." />;
  }
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

export function KnowledgeResultTable({ results }: { results: CanonicalKnowledgeResult[] }) {
  if (results.length === 0) {
    return <EmptyState title="No Knowledge matches" detail="No authorized source-backed results matched this query." />;
  }
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
