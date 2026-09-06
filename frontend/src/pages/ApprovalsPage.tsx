import { useCallback, useEffect, useState } from "react";
import {
  ApprovalClient,
  type CanonicalApproval,
} from "../api/approvals";
import type { Page } from "../api/types";
import type { ApprovalDecisionManifestState } from "../app/approvalManifest";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  Card,
  CanonicalId,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

interface ApprovalSurfaceProps {
  client: ApprovalClient;
  decisionState: ApprovalDecisionManifestState;
}

export function ApprovalsPage({ client, decisionState }: ApprovalSurfaceProps) {
  const [showAll, setShowAll] = useState(false);
  const [page, setPage] = useState<Page<CanonicalApproval> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const pagination = useCursorPagination(`approvals:${showAll ? "all" : "pending"}:id:asc`);

  const load = useCallback(async () => {
    try {
      setPage(
        await client.listApprovals({
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
          Inspect exact-action approval records without exposing proposed payload values. Approve
          and Deny are offered only when the Control Plane advertises the shared canonical decision
          commands; otherwise this surface remains explicitly read-only.
        </p>
      </header>

      {decisionState === "unavailable" ? (
        <DegradedState
          title="Approval decisions unavailable"
          detail="The canonical Approval collection remains available for inspection, but the Control Plane does not advertise both safe decision commands. No private or client-side fallback is used."
        />
      ) : null}
      {decisionState === "loading" ? (
        <LoadingState label="Checking Approval decision capability…" />
      ) : null}

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
  decisionState,
}: ApprovalSurfaceProps & {
  approvalId: string;
}) {
  const [approval, setApproval] = useState<CanonicalApproval | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [decisionError, setDecisionError] = useState<unknown>(null);
  const [comment, setComment] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setApproval(await client.getApproval(approvalId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [approvalId, client]);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (decision: "approve" | "deny") => {
    if (approval === null || approval.status !== "pending" || decisionState !== "available") return;
    setBusy(true);
    setDecisionError(null);
    try {
      const options = comment.trim() ? { comment } : {};
      const updated = decision === "approve"
        ? await client.approve(approval.id, approval.requested_action_digest, options)
        : await client.deny(approval.id, approval.requested_action_digest, options);
      setApproval(updated);
      setComment("");
      setConfirmed(false);
    } catch (nextError) {
      setDecisionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error && approval === null) return <ErrorState error={error} onRetry={() => void load()} />;
  if (approval === null) return <LoadingState />;

  return (
    <ApprovalDetailView
      approval={approval}
      decisionState={decisionState}
      loadError={error}
      decisionError={decisionError}
      comment={comment}
      confirmed={confirmed}
      busy={busy}
      onRetry={() => void load()}
      onComment={setComment}
      onConfirmed={setConfirmed}
      onDecision={decide}
    />
  );
}

export function ApprovalDetailView({
  approval,
  decisionState,
  loadError,
  decisionError,
  comment,
  confirmed,
  busy,
  onRetry,
  onComment,
  onConfirmed,
  onDecision,
}: {
  approval: CanonicalApproval;
  decisionState: ApprovalDecisionManifestState;
  loadError: unknown;
  decisionError: unknown;
  comment: string;
  confirmed: boolean;
  busy: boolean;
  onRetry: () => void;
  onComment: (value: string) => void;
  onConfirmed: (value: boolean) => void;
  onDecision: (decision: "approve" | "deny") => Promise<void>;
}) {
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

      {loadError ? <ErrorState error={loadError} onRetry={onRetry} /> : null}

      <div className="grid-two">
        <Card title="Exact action binding">
          <DefinitionList
            values={{
              approval_id: approval.id,
              action: approval.action,
              resource_type: approval.resource_type,
              resource_id: approval.resource_id,
              subject_type: approval.subject_type,
              subject_id: approval.subject_id,
              requested_action_digest: approval.requested_action_digest,
              policy: approval.policy_id,
              risk: approval.risk,
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

      <ApprovalDecisionPanel
        approval={approval}
        decisionState={decisionState}
        comment={comment}
        confirmed={confirmed}
        busy={busy}
        error={decisionError}
        onComment={onComment}
        onConfirmed={onConfirmed}
        onDecision={onDecision}
      />

      <Card title="Security boundary">
        <p>
          The browser receives the canonical Approval projection only: action/resource/risk/policy
          context, references and the immutable requested-action digest. It never reconstructs or
          sends hidden proposed payload values. Decision authorization, actor validation, expiry,
          terminal-state checks, exact-action binding and audit enforcement remain server-side.
        </p>
      </Card>
    </div>
  );
}

function ApprovalDecisionPanel({
  approval,
  decisionState,
  comment,
  confirmed,
  busy,
  error,
  onComment,
  onConfirmed,
  onDecision,
}: {
  approval: CanonicalApproval;
  decisionState: ApprovalDecisionManifestState;
  comment: string;
  confirmed: boolean;
  busy: boolean;
  error: unknown;
  onComment: (value: string) => void;
  onConfirmed: (value: boolean) => void;
  onDecision: (decision: "approve" | "deny") => Promise<void>;
}) {
  if (decisionState === "loading") {
    return (
      <Card title="Decision">
        <LoadingState label="Checking canonical Approval decision commands…" />
      </Card>
    );
  }
  if (decisionState === "unavailable") {
    return (
      <Card title="Decision">
        <DegradedState
          title="Read-only Approval surface"
          detail="The Control Plane does not advertise both approval.approve and approval.deny. The browser will not call a private Approval service or invent a fallback mutation."
        />
      </Card>
    );
  }
  if (approval.status !== "pending") {
    return (
      <Card title="Decision">
        <DegradedState
          title={`Approval is ${approval.status}`}
          detail="Only canonical pending Approvals can be decided. The server remains authoritative for expiry and lifecycle state."
        />
      </Card>
    );
  }

  return (
    <Card title="Decision">
      <div className="stack">
        <p>
          Review the exact canonical context below before deciding. Frontend controls are not an
          authorization boundary; the Control Plane will independently authorize the authenticated
          approver and validate this digest again.
        </p>
        <DefinitionList
          values={{
            approval_id: approval.id,
            action: approval.action,
            resource: `${approval.resource_type}:${approval.resource_id}`,
            risk: approval.risk,
            policy: approval.policy_id,
            requested_action_digest: approval.requested_action_digest,
            expires: formatDate(approval.expires_at),
          }}
        />
        <label className="field field-wide">
          <span>Decision comment (optional)</span>
          <textarea
            rows={4}
            value={comment}
            disabled={busy}
            onChange={(event) => onComment(event.target.value)}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={confirmed}
            disabled={busy}
            onChange={(event) => onConfirmed(event.target.checked)}
          />
          I confirm this exact Approval ID, action, resource, policy, risk and requested-action digest.
        </label>
        {error ? <ErrorState error={error} /> : null}
        <div className="actions">
          <button disabled={!confirmed || busy} onClick={() => void onDecision("approve")}>
            {busy ? "Submitting…" : "Approve"}
          </button>
          <button disabled={!confirmed || busy} onClick={() => void onDecision("deny")}>
            {busy ? "Submitting…" : "Deny"}
          </button>
        </div>
      </div>
    </Card>
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
