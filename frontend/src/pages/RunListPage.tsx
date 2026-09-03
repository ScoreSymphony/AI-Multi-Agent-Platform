import { useCallback, useEffect, useState } from "react";
import { ControlPlaneClient } from "../api/client";
import type { CanonicalRun, Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  CanonicalId,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const RUN_QUERY_KEY = "runs:updated_at:desc";

export function RunsPage({ client }: { client: ControlPlaneClient }) {
  const [page, setPage] = useState<Page<CanonicalRun> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const pagination = useCursorPagination(RUN_QUERY_KEY);

  const load = useCallback(async () => {
    try {
      setPage(
        await client.listRuns({
          limit: 100,
          cursor: pagination.cursor,
          sort: "updated_at",
          direction: "desc",
        }),
      );
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, pagination.cursor]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Execution</p>
        <h1>Runs</h1>
        <p>Canonical attempts across Tasks.</p>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {!page ? <LoadingState /> : (
        <>
          <RunTable runs={page.items} />
          <PaginationControls
            page={page}
            pageNumber={pagination.pageNumber}
            hasPrevious={pagination.hasPrevious}
            onPrevious={pagination.previous}
            onRefresh={() => void load()}
            onNext={() => pagination.next(page.next_cursor)}
          />
        </>
      )}
    </div>
  );
}

function RunTable({ runs }: { runs: CanonicalRun[] }) {
  if (runs.length === 0) return <EmptyState title="No runs" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Run</th><th>Status</th><th>Task</th><th>Attempt</th></tr></thead>
        <tbody>{runs.map((run) => (
          <tr key={run.id}>
            <td><AppLink href={`/runs/${run.id}`}><CanonicalId value={run.id} /></AppLink></td>
            <td><StatusBadge value={run.status} /></td>
            <td><AppLink href={`/tasks/${run.task_id}`}><CanonicalId value={run.task_id} /></AppLink></td>
            <td>{run.attempt}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}