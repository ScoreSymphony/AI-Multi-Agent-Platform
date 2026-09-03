import { useCallback, useEffect, useState } from "react";
import type { CanonicalApproval } from "../api/approvals";
import { ControlPlaneCollectionClient } from "../api/collections";
import type { Page } from "../api/types";
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

const APPROVAL_COLLECTION = "approvals";

export function ApprovalsPage({ client }: { client: ControlPlaneCollectionClient }) {
  const [showAll, setShowAll] = useState(false);
  const [page, setPage] = useState<Page<CanonicalApproval> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const pagination = useCursorPagination(`approvals:${showAll ? "all" : "pending"}:id:asc`);

  const load = useCallback(async () => {
    try {
      setPage(
        await client.list<CanonicalApproval>(APPROVAL_COLLECTION, {
          limit: 50,
          cursor: pagination.cursor,
          sort: "id",
          direction: "asc",
          filters: showAll ? {} : { status: "pending" },
        }),
      );
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, pagination.cursor, showAll]);

  useEffect(() => {
    void load();
  }, [load]);

  const pendingOnPage = page?.items.filter((approval) => approval.status === "pending").length ?? "—";
  const elevatedOnPage = page?.items.filter((approval) => approval.risk !== "low").length ?? "—";

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical security approvals</p>
        <h1>Approvals</h1>
        <p>
          Read-only inspection of exact-action approval records. Proposed action payload values are
          not exposed by the Control Plane, and this UI does not invent approve or deny mutations.
        </p>
      </header>

      <div className="metrics">
        <Metric label={showAll ? "Approvals" : "Pending approvals"} value={page?.total ?? "—"} />
        <Metric label="Pending on page" value={pendingOnPage} />
        <Metric label="Non-low risk on page" value={elevatedOnPage} />
      </div>

      <Card title="Approval queue">
        <div className="actions">
          <label>
            <input
              checked={showAll}
              onChange={(event) => setShowAll(event.target.checked)}
              type="checkbox"
            />
            Show decided and expired approvals
          </label>
          <button onClick={() => void load()}>Refresh</button>
        </div>
        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
        {!page && !error ? <LoadingState /> : null}
        {page ? <ApprovalTable approvals={page.items} /> : null}
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
    </div>
  );
}

export function ApprovalDetailPage({
  client,
  approvalId,
}: {
  client: ControlPlaneCollectionClient;
  approvalId: string;
}) {
  const [approval, setApproval] = useState<CanonicalApproval | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setApproval(await client.get<CanonicalApproval>(APPROVAL_COLLECTION, approvalId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [approvalId, client]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && approval === null) return <ErrorState error={error} onRetry={() => void load()} />;
  if (approval === null) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Approval record</p>
          <h1>{approval.action}</h1>
          <CanonicalId value={approval.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={approval.status} />
          <span>{approval.risk} risk</span>
        </div>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      <div className="grid-two">
        <Card title="Exact action binding">
          <DefinitionList
            values={{
              action: approval.action,
              resource_type: approval.resource_type,
              resource_id: approval.resource_id,
              subject_type: approval.subject_type,
              subject_id: approval.subject_id,
              digest: approval.requested_action_digest,
              policy: approval.policy_id,
            }}
          />
        </Card>
        <Card title="Request context">
          <DefinitionList
            values={{
              requester: approval.requester_ref,
              owner: `${approval.owner_ref.type}:${approval.owner_ref.id}`,
              project: approval.project_id ?? "—",
              capability: approval.capability_ref ?? "—",
              payload_reference: approval.payload_ref ?? "—",
              reason: approval.reason,
            }}
          />
        </Card>
      </div>

      <div className="grid-two">
        <Card title="Task & Run evidence">
          <ReferenceLink label="Task" value={approval.task_id} hrefPrefix="/tasks/" />
          <ReferenceLink label="Run" value={approval.run_id} hrefPrefix="/runs/" />
        </Card>
        <Card title="Lifecycle">
          <DefinitionList
            values={{
              created: formatDate(approval.created_at),
              expires: formatDate(approval.expires_at),
              decided_at: formatDate(approval.decision_at),
              decided_by: approval.decision_by
                ? `${approval.decision_by.type}:${approval.decision_by.id}`
                : "—",
              decision_comment: approval.decision_comment ?? "—",
            }}
          />
        </Card>
      </div>

      <Card title="Security boundary">
        <p>
          The canonical approval projection deliberately exposes the action digest and references,
          not proposed payload values. Decision authority remains inside #15; this page is
          inspection-only until an exact canonical decision route is available.
        </p>
      </Card>
    </div>
  );
}

function ApprovalTable({ approvals }: { approvals: CanonicalApproval[] }) {
  if (approvals.length === 0) {
    return <EmptyState title="No approvals in this view" />;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Approval</th><th>Status</th><th>Risk</th><th>Action</th><th>Resource</th><th>Task</th><th>Expires</th></tr>
        </thead>
        <tbody>
          {approvals.map((approval) => (
            <tr key={approval.id}>
              <td>
                <AppLink href={`/approvals/${encodeURIComponent(approval.id)}`}>
                  <CanonicalId value={approval.id} />
                </AppLink>
              </td>
              <td><StatusBadge value={approval.status} /></td>
              <td>{approval.risk}</td>
              <td>{approval.action}</td>
              <td>{approval.resource_type}:{approval.resource_id}</td>
              <td>
                {approval.task_id ? (
                  <AppLink href={`/tasks/${encodeURIComponent(approval.task_id)}`}>
                    <CanonicalId value={approval.task_id} />
                  </AppLink>
                ) : "—"}
              </td>
              <td>{formatDate(approval.expires_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return (
    <dl className="definition-list">
      {Object.entries(values).map(([label, value]) => (
        <div key={label}><dt>{label.replaceAll("_", " ")}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function ReferenceLink({
  label,
  value,
  hrefPrefix,
}: {
  label: string;
  value: string | null;
  hrefPrefix: string;
}) {
  return (
    <p>
      <strong>{label}: </strong>
      {value ? (
        <AppLink href={`${hrefPrefix}${encodeURIComponent(value)}`}>
          <CanonicalId value={value} />
        </AppLink>
      ) : "—"}
    </p>
  );
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
