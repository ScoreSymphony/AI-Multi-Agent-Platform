import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type CanonicalVerification,
  type CanonicalVerificationRequirement,
  type HumanReviewInput,
  VerificationClient,
  type VerificationReviewAction,
} from "../api/verification";
import type { Page } from "../api/types";
import { AppLink } from "../app/router";
import {
  Card,
  CanonicalId,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

export function VerificationPage({ client }: { client: VerificationClient }) {
  const [pending, setPending] = useState<Page<CanonicalVerification> | null>(null);
  const [history, setHistory] = useState<Page<CanonicalVerification> | null>(null);
  const [requirements, setRequirements] = useState<Page<CanonicalVerificationRequirement> | null>(
    null,
  );
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const [nextPending, nextHistory, nextRequirements] = await Promise.all([
        client.listPendingReviews({ limit: 50, sort: "created_at", direction: "asc" }),
        client.list({ limit: 50, sort: "created_at", direction: "desc" }),
        client.listRequirements({ limit: 50, sort: "updated_at", direction: "desc" }),
      ]);
      setPending(nextPending);
      setHistory(nextHistory);
      setRequirements(nextRequirements);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const blocked = requirements?.items.filter(
    (requirement) => requirement.completion.state !== "accepted",
  ).length;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical runtime review</p>
        <h1>Verification</h1>
        <p>
          Review exact Result or Artifact revisions. Actions below record canonical Verification
          results; they do not directly complete Tasks or grant security approval.
        </p>
      </header>

      <div className="metrics">
        <Metric label="Pending human reviews" value={pending?.total ?? "—"} />
        <Metric label="Verification records" value={history?.total ?? "—"} />
        <Metric label="Blocked requirements on page" value={blocked ?? "—"} />
      </div>

      <div className="actions">
        <button onClick={() => void load()}>Refresh</button>
      </div>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {!pending && !history && !requirements && !error ? <LoadingState /> : null}

      <Card title="Pending review queue">
        {pending ? <VerificationTable verifications={pending.items} empty="No pending human reviews" /> : <LoadingState />}
      </Card>

      <Card title="Completion requirements">
        {requirements ? (
          <RequirementTable requirements={requirements.items} />
        ) : (
          <LoadingState />
        )}
      </Card>

      <Card title="Verification history">
        {history ? (
          <VerificationTable verifications={history.items} empty="No verification history" />
        ) : (
          <LoadingState />
        )}
      </Card>
    </div>
  );
}

export function VerificationDetailPage({
  client,
  verificationId,
}: {
  client: VerificationClient;
  verificationId: string;
}) {
  const [verification, setVerification] = useState<CanonicalVerification | null>(null);
  const [requirement, setRequirement] = useState<CanonicalVerificationRequirement | null>(null);
  const [history, setHistory] = useState<CanonicalVerification[]>([]);
  const [comment, setComment] = useState("");
  const [evidenceText, setEvidenceText] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [retryAttempt, setRetryAttempt] = useState<ReviewAttempt | null>(null);

  const load = useCallback(async () => {
    try {
      const nextVerification = await client.get(verificationId);
      setVerification(nextVerification);
      const [nextRequirement, nextHistory] = await Promise.all([
        client.getRequirement(nextVerification.task_id).catch(() => null),
        client.list({
          limit: 100,
          sort: "created_at",
          direction: "asc",
          filters: { task_id: nextVerification.task_id },
        }),
      ]);
      setRequirement(nextRequirement);
      setHistory(nextHistory.items);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, verificationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const evidenceArtifactIds = useMemo(() => parseEvidenceIds(evidenceText), [evidenceText]);
  const canReview =
    verification?.status === "pending" && verification.requested_verifier_kind === "human";

  const submit = useCallback(
    async (action: VerificationReviewAction) => {
      if (!verification) return;
      const input: HumanReviewInput = {
        ...(comment.trim() ? { comment: comment.trim() } : {}),
        ...(evidenceArtifactIds.length ? { evidence_artifact_ids: evidenceArtifactIds } : {}),
      };
      const fingerprint = JSON.stringify({ action, input });
      const key =
        retryAttempt?.fingerprint === fingerprint ? retryAttempt.idempotencyKey : crypto.randomUUID();
      setRetryAttempt({ action, fingerprint, idempotencyKey: key });
      setBusy(true);
      setActionError(null);
      try {
        if (action === "accept") await client.accept(verification.id, input, key);
        else if (action === "reject") await client.reject(verification.id, input, key);
        else await client.requestChanges(verification.id, input, key);
        setRetryAttempt(null);
        setComment("");
        setEvidenceText("");
        await load();
      } catch (nextError) {
        setActionError(nextError);
      } finally {
        setBusy(false);
      }
    }, [client, comment, evidenceArtifactIds, load, retryAttempt, verification],
  );

  if (error && verification === null) {
    return <ErrorState error={error} onRetry={() => void load()} />;
  }
  if (verification === null) return <LoadingState />;

  return (
    <VerificationDetailView
      verification={verification}
      requirement={requirement}
      history={history}
      comment={comment}
      evidenceText={evidenceText}
      busy={busy}
      actionError={actionError}
      canReview={canReview}
      onComment={setComment}
      onEvidence={setEvidenceText}
      onReview={(action) => void submit(action)}
      onRefresh={() => void load()}
    />
  );
}

interface ReviewAttempt {
  action: VerificationReviewAction;
  fingerprint: string;
  idempotencyKey: string;
}

export function VerificationDetailView({
  verification,
  requirement,
  history,
  comment,
  evidenceText,
  busy,
  actionError,
  canReview,
  onComment,
  onEvidence,
  onReview,
  onRefresh,
}: {
  verification: CanonicalVerification;
  requirement: CanonicalVerificationRequirement | null;
  history: CanonicalVerification[];
  comment: string;
  evidenceText: string;
  busy: boolean;
  actionError: unknown;
  canReview: boolean;
  onComment: (value: string) => void;
  onEvidence: (value: string) => void;
  onReview: (action: VerificationReviewAction) => void;
  onRefresh: () => void;
}) {
  const result = verification.verification_result;
  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Verification record</p>
          <h1>{verification.stage_id}</h1>
          <CanonicalId value={verification.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={result?.outcome ?? verification.status} />
          <span>{verification.requested_verifier_kind} review</span>
        </div>
      </header>

      <div className="actions">
        <button onClick={onRefresh}>Refresh</button>
      </div>

      <div className="grid-two">
        <Card title="Exact subject binding">
          <DefinitionList
            values={{
              type: verification.subject.type,
              id: verification.subject.id,
              revision: verification.subject.revision,
              digest: verification.subject.digest,
              policy: `${verification.policy.id}@${verification.policy.version}`,
              stage: verification.stage_id,
              repair_attempt: verification.repair_attempt,
            }}
          />
        </Card>
        <Card title="Runtime context">
          <ReferenceLink label="Task" value={verification.task_id} hrefPrefix="/tasks/" />
          <ReferenceLink label="Run" value={verification.run_id} hrefPrefix="/runs/" />
          <ReferenceLink label="Result" value={verification.result_id} hrefPrefix="/results/" />
          <DefinitionList
            values={{
              project: verification.project_id ?? "—",
              requested_verifier: verification.requested_verifier_kind,
              requested_capability: verification.requested_capability_ref ?? "—",
              created: formatDate(verification.created_at),
              expires: formatDate(verification.expires_at),
            }}
          />
        </Card>
      </div>

      {requirement ? (
        <Card title="Task completion policy">
          <div className="detail-status">
            <StatusBadge value={requirement.completion.state} />
            <span>{requirement.completion.reason}</span>
          </div>
          <DefinitionList
            values={{
              policy: `${requirement.policy.id}@${requirement.policy.version}`,
              repair_attempts_remaining: requirement.completion.repair_attempts_remaining,
              blocking_verifications:
                requirement.completion.blocking_verification_ids.join(", ") || "—",
              exact_subject: requirement.subject
                ? `${requirement.subject.id}@${requirement.subject.revision}`
                : "—",
            }}
          />
        </Card>
      ) : null}

      {canReview ? (
        <Card title="Human review decision">
          <p>
            This action records a VerificationResult for the exact digest above. It does not
            directly transition the Task or approve later privileged actions.
          </p>
          <label className="stack">
            <span>Comment</span>
            <textarea
              value={comment}
              onChange={(event) => onComment(event.target.value)}
              rows={4}
              disabled={busy}
              placeholder="Optional review finding"
            />
          </label>
          <label className="stack">
            <span>Evidence Artifact IDs</span>
            <input
              value={evidenceText}
              onChange={(event) => onEvidence(event.target.value)}
              disabled={busy}
              placeholder="artifact_… , artifact_…"
            />
          </label>
          {actionError ? <ErrorState error={actionError} /> : null}
          <div className="actions" aria-label="Verification review actions">
            <button className="primary" disabled={busy} onClick={() => onReview("accept")}>
              Accept
            </button>
            <button disabled={busy} onClick={() => onReview("request-changes")}>
              Request changes
            </button>
            <button disabled={busy} onClick={() => onReview("reject")}>
              Reject
            </button>
          </div>
        </Card>
      ) : null}

      {result ? <VerificationResultCard verification={verification} /> : null}

      <Card title="Task verification history">
        <VerificationTable verifications={history} empty="No other verification records" />
      </Card>
    </div>
  );
}

function VerificationResultCard({ verification }: { verification: CanonicalVerification }) {
  const result = verification.verification_result;
  if (!result) return null;
  return (
    <Card title="Recorded verification result">
      <div className="detail-status">
        <StatusBadge value={result.outcome} />
        <span>{result.verifier.kind} · {result.verifier.ref}</span>
      </div>
      <DefinitionList
        values={{
          verifier_agent: result.verifier.agent_id ?? "—",
          agent_revision: result.verifier.agent_revision ?? "—",
          model: result.verifier.model_config_id ?? "—",
          provider: result.verifier.provider_id ?? "—",
          read_only: String(result.verifier.read_only),
          checks: result.checks_executed.join(", ") || "—",
          completed: formatDate(result.completed_at),
        }}
      />
      <h3>Findings</h3>
      {result.findings.length ? (
        <ul>
          {result.findings.map((finding, index) => (
            <li key={`${finding.code}:${index}`}>
              <strong>{finding.severity} · {finding.code}:</strong> {finding.message}
              {finding.location_ref ? ` (${finding.location_ref})` : ""}
            </li>
          ))}
        </ul>
      ) : (
        <p>No findings recorded.</p>
      )}
      <h3>Evidence</h3>
      {result.evidence_artifact_ids.length ? (
        <ul>
          {result.evidence_artifact_ids.map((artifactId) => (
            <li key={artifactId}>
              <AppLink href={`/artifacts/${encodeURIComponent(artifactId)}`}>
                <CanonicalId value={artifactId} />
              </AppLink>
            </li>
          ))}
        </ul>
      ) : (
        <p>No evidence Artifacts recorded.</p>
      )}
      {result.errors.length ? (
        <>
          <h3>Verifier errors</h3>
          <ul>
            {result.errors.map((item, index) => (
              <li key={`${item.code}:${index}`}>{item.code}: {item.message}</li>
            ))}
          </ul>
        </>
      ) : null}
    </Card>
  );
}

export function VerificationTable({
  verifications,
  empty,
}: {
  verifications: CanonicalVerification[];
  empty: string;
}) {
  if (!verifications.length) return <EmptyState title={empty} />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Verification</th><th>Status / outcome</th><th>Subject</th><th>Task</th><th>Reviewer</th><th>Created</th>
          </tr>
        </thead>
        <tbody>
          {verifications.map((verification) => (
            <tr key={verification.id}>
              <td>
                <AppLink href={`/verification/${encodeURIComponent(verification.id)}`}>
                  <CanonicalId value={verification.id} />
                </AppLink>
              </td>
              <td><StatusBadge value={verification.verification_result?.outcome ?? verification.status} /></td>
              <td>{verification.subject.type}:{verification.subject.id}@{verification.subject.revision}</td>
              <td>
                <AppLink href={`/tasks/${encodeURIComponent(verification.task_id)}`}>
                  <CanonicalId value={verification.task_id} />
                </AppLink>
              </td>
              <td>{verification.verification_result?.verifier.ref ?? verification.requested_verifier_kind}</td>
              <td>{formatDate(verification.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RequirementTable({
  requirements,
}: {
  requirements: CanonicalVerificationRequirement[];
}) {
  if (!requirements.length) return <EmptyState title="No verification requirements" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Task</th><th>Completion</th><th>Policy</th><th>Exact subject</th><th>Repair budget</th></tr>
        </thead>
        <tbody>
          {requirements.map((requirement) => (
            <tr key={requirement.task_id}>
              <td>
                <AppLink href={`/tasks/${encodeURIComponent(requirement.task_id)}`}>
                  <CanonicalId value={requirement.task_id} />
                </AppLink>
              </td>
              <td><StatusBadge value={requirement.completion.state} /></td>
              <td>{requirement.policy.id}@{requirement.policy.version}</td>
              <td>{requirement.subject ? `${requirement.subject.id}@${requirement.subject.revision}` : "—"}</td>
              <td>{requirement.completion.repair_attempts_remaining}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
      ) : (
        "—"
      )}
    </p>
  );
}

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return (
    <dl className="definition-list">
      {Object.entries(values).map(([label, value]) => (
        <div key={label}>
          <dt>{label.replaceAll("_", " ")}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="metric">
      <span>{label}</span><strong>{value}</strong>
    </div>
  );
}

function parseEvidenceIds(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
