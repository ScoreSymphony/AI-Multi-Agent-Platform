import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type CanonicalLearningCandidate,
  type CanonicalLearningFeedback,
  type CanonicalPostPromotionEvaluation,
  LearningClient,
} from "../api/learning";
import type { Page } from "../api/types";
import { AppLink } from "../app/router";
import {
  Card,
  CanonicalId,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

export const LEARNING_ROUTE = "/learning";
export const LEARNING_REQUIRED_RESOURCES = [
  "learning-candidates",
  "learning-feedback",
] as const;
export const LEARNING_OPTIONAL_RESOURCES = [
  "learning-post-promotion-evaluations",
] as const;
export const LEARNING_DECISION_COMMANDS = [
  "learning.evidence",
  "learning.accept",
  "learning.reject",
  "learning.supersede",
  "learning.promote",
] as const;

export interface LearningSurfaceProps {
  client: LearningClient;
  commands?: readonly string[];
  postPromotionAvailable?: boolean;
}

export function LearningPage({ client }: LearningSurfaceProps) {
  const [candidates, setCandidates] = useState<Page<CanonicalLearningCandidate> | null>(null);
  const [feedback, setFeedback] = useState<Page<CanonicalLearningFeedback> | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const [candidatePage, feedbackPage] = await Promise.all([
        client.listCandidates({ limit: 100, sort: "id", direction: "asc" }),
        client.listFeedback({ limit: 50, sort: "id", direction: "desc" }),
      ]);
      setCandidates(candidatePage);
      setFeedback(feedbackPage);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = useMemo(() => {
    const values = candidates?.items ?? [];
    return {
      proposed: values.filter((item) => item.status === "proposed").length,
      evaluating: values.filter((item) => item.status === "evaluating").length,
      accepted: values.filter((item) => item.status === "accepted").length,
      promoted: values.filter((item) => item.status === "promoted").length,
    };
  }, [candidates]);

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Governed improvement pipeline</p>
        <h1>Learning</h1>
        <p>
          Inspect non-authoritative Learning Candidates, their exact source evidence, evaluation
          gates, Approval bindings and canonical owner revisions. Promotion never grants new
          permissions and never mutates historical evidence.
        </p>
      </header>

      <div className="metrics">
        <Metric label="Candidates" value={candidates?.total ?? "—"} />
        <Metric label="Proposed" value={counts.proposed} />
        <Metric label="Evaluating" value={counts.evaluating} />
        <Metric label="Accepted" value={counts.accepted} />
        <Metric label="Promoted" value={counts.promoted} />
        <Metric label="Feedback records" value={feedback?.total ?? "—"} />
      </div>

      <Card title="Learning Candidates">
        <div className="actions">
          <button onClick={() => void load()}>Refresh</button>
        </div>
        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
        {!candidates && !error ? <LoadingState /> : null}
        {candidates ? <CandidateTable candidates={candidates.items} /> : null}
      </Card>

      <Card title="Recent explicit feedback">
        {!feedback && !error ? <LoadingState /> : null}
        {feedback ? <FeedbackTable feedback={feedback.items} /> : null}
      </Card>
    </div>
  );
}

export function LearningDetailPage({
  client,
  candidateId,
  commands = [],
  postPromotionAvailable = false,
}: LearningSurfaceProps & { candidateId: string }) {
  const [candidate, setCandidate] = useState<CanonicalLearningCandidate | null>(null);
  const [postPromotion, setPostPromotion] = useState<CanonicalPostPromotionEvaluation[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [decisionError, setDecisionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [approvalId, setApprovalId] = useState("");
  const commandSet = useMemo(() => new Set(commands), [commands]);

  const load = useCallback(async () => {
    try {
      const current = await client.getCandidate(candidateId);
      setCandidate(current);
      if (postPromotionAvailable) {
        const page = await client.listPostPromotionEvaluations({ limit: 200 });
        setPostPromotion(
          page.items.filter((item) => item.learning_candidate_id === current.id),
        );
      }
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [candidateId, client, postPromotionAvailable]);

  useEffect(() => {
    void load();
  }, [load]);

  const mutate = async (
    operation: (current: CanonicalLearningCandidate) => Promise<CanonicalLearningCandidate>,
  ) => {
    if (candidate === null) return;
    setBusy(true);
    setDecisionError(null);
    try {
      setCandidate(await operation(candidate));
      await load();
    } catch (nextError) {
      setDecisionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error && candidate === null) return <ErrorState error={error} onRetry={() => void load()} />;
  if (candidate === null) return <LoadingState />;

  const canAccept = commandSet.has("learning.accept")
    && (candidate.status === "proposed" || candidate.status === "evaluating");
  const canReject = commandSet.has("learning.reject")
    && !["rejected", "superseded", "promoted"].includes(candidate.status);
  const canPromote = commandSet.has("learning.promote") && candidate.status === "accepted";

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Learning Candidate</p>
          <h1>{candidate.improvement_type}</h1>
          <CanonicalId value={candidate.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={candidate.status} />
          <span>{candidate.risk} risk</span>
        </div>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      <div className="grid-two">
        <Card title="Problem & hypothesis">
          <p>{candidate.problem}</p>
          <DefinitionList
            values={{
              expected_benefit: candidate.expected_benefit,
              source_type: candidate.source_type,
              creator: candidate.creator_ref,
              project: candidate.project_id ?? "—",
              candidate_revision: candidate.revision,
              candidate_digest: candidate.content_digest,
            }}
          />
        </Card>
        <Card title="Exact target binding">
          <DefinitionList
            values={{
              resource_type: candidate.target.resource_type,
              resource_id: candidate.target.resource_id,
              base_revision: candidate.target.revision,
              promoted_revision: candidate.promotion?.new_revision ?? "—",
              canonical_ref: candidate.promotion?.canonical_ref ?? "—",
            }}
          />
        </Card>
      </div>

      <div className="grid-two">
        <Card title="Quality gate">
          <DefinitionList
            values={{
              policy: `${candidate.gate_plan.policy_id}@${candidate.gate_plan.policy_version}`,
              require_evaluation: candidate.gate_plan.require_evaluation,
              require_verification: candidate.gate_plan.require_verification,
              regression_free: candidate.gate_plan.require_regression_free,
              automatic_promotion: candidate.gate_plan.automatic_promotion_allowed,
              evaluation_runs: candidate.evaluation_run_ids.join(", ") || "—",
              verification_ids: candidate.verification_ids.join(", ") || "—",
            }}
          />
        </Card>
        <Card title="Approval binding">
          {candidate.approvals.length === 0 ? (
            <EmptyState title="No Approval requested" detail="No exact-action Approval is bound." />
          ) : (
            <ul className="reference-list">
              {candidate.approvals.map((approval) => (
                <li key={approval.approval_id}>
                  <strong>{approval.status}</strong> · {approval.risk} · {approval.policy_id}
                  <br />
                  <CanonicalId value={approval.approval_id} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Proposed owner change">
        <pre>{JSON.stringify(candidate.proposed_change, null, 2)}</pre>
      </Card>

      <div className="grid-two">
        <ReferenceCard title="Source evidence" references={candidate.source_refs} />
        <ReferenceCard title="Gate evidence" references={candidate.evidence_refs} />
      </div>

      <Card title="Immutable candidate history">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Revision</th>
                <th>Status</th>
                <th>Evaluation</th>
                <th>Verification</th>
                <th>Digest</th>
              </tr>
            </thead>
            <tbody>
              {candidate.history.map((entry) => (
                <tr key={entry.revision}>
                  <td>{entry.revision}</td>
                  <td><StatusBadge value={entry.status} /></td>
                  <td>{entry.evaluation_run_ids.join(", ") || "—"}</td>
                  <td>{entry.verification_ids.join(", ") || "—"}</td>
                  <td><CanonicalId value={entry.content_digest} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Post-promotion Evaluation">
        {!postPromotionAvailable ? (
          <DegradedState
            title="Post-promotion collection not registered"
            detail="The candidate remains inspectable. The integration branch can register the optional derived Evaluation collection without changing promotion authority."
          />
        ) : postPromotion.length === 0 ? (
          <EmptyState
            title="No post-promotion Evaluation recorded"
            detail="No configured post-promotion suite has produced derived evidence for this Candidate."
          />
        ) : (
          <ul className="reference-list">
            {postPromotion.map((record) => (
              <li key={record.id}>
                <StatusBadge value={record.outcome} /> target revision {record.target_revision}
                <br />
                Runs: {record.evaluation_run_ids.join(", ") || "—"}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Decision">
        {decisionError ? <ErrorState error={decisionError} /> : null}
        <div className="actions">
          <button
            disabled={!canAccept || busy}
            onClick={() => void mutate((current) => client.accept(current.id, current.revision))}
          >
            Accept
          </button>
          <button
            disabled={!canReject || busy}
            onClick={() => void mutate((current) => client.reject(current.id, current.revision))}
          >
            Reject
          </button>
        </div>
        <label>
          Approval ID for gated promotion
          <input
            value={approvalId}
            onChange={(event) => setApprovalId(event.target.value)}
            placeholder="approval_…"
          />
        </label>
        <button
          disabled={!canPromote || busy}
          onClick={() => void mutate((current) => client.promote(
            current.id,
            current.revision,
            approvalId.trim() || undefined,
          ))}
        >
          Promote through canonical owner service
        </button>
        {!commandSet.has("learning.accept") || !commandSet.has("learning.promote") ? (
          <DegradedState
            title="Some Learning decision commands are unavailable"
            detail="The page never invents a private mutation fallback. Missing commands remain disabled until advertised by the canonical Control Plane manifest."
          />
        ) : null}
      </Card>
    </div>
  );
}

function CandidateTable({ candidates }: { candidates: CanonicalLearningCandidate[] }) {
  if (candidates.length === 0) {
    return <EmptyState title="No Learning Candidates" detail="No governed proposals are recorded." />;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Improvement</th>
            <th>Target</th>
            <th>Source</th>
            <th>Risk</th>
            <th>Revision</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((candidate) => (
            <tr key={candidate.id}>
              <td><StatusBadge value={candidate.status} /></td>
              <td><AppLink href={`${LEARNING_ROUTE}/${candidate.id}`}>{candidate.improvement_type}</AppLink></td>
              <td>{candidate.target.resource_type}:{candidate.target.resource_id}@{candidate.target.revision}</td>
              <td>{candidate.source_type}</td>
              <td>{candidate.risk}</td>
              <td>{candidate.revision}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FeedbackTable({ feedback }: { feedback: CanonicalLearningFeedback[] }) {
  if (feedback.length === 0) {
    return <EmptyState title="No explicit feedback" detail="No canonical feedback records exist." />;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Type</th><th>Subject</th><th>Creator</th><th>Comment</th></tr>
        </thead>
        <tbody>
          {feedback.map((item) => (
            <tr key={item.id}>
              <td>{item.feedback_type}</td>
              <td>{item.subject.kind}:{item.subject.resource_id}</td>
              <td>{item.creator_ref}</td>
              <td>{item.comment ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReferenceCard({
  title,
  references,
}: {
  title: string;
  references: CanonicalLearningCandidate["source_refs"];
}) {
  return (
    <Card title={title}>
      {references.length === 0 ? (
        <EmptyState title="No references" detail="No canonical references are attached." />
      ) : (
        <ul className="reference-list">
          {references.map((reference) => (
            <li key={`${reference.kind}:${reference.resource_id}:${String(reference.revision)}`}>
              <strong>{reference.kind}</strong> · {reference.resource_id}
              {reference.revision === null ? "" : ` @ ${reference.revision}`}
              {reference.digest ? <><br /><CanonicalId value={reference.digest} /></> : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function DefinitionList({ values }: { values: Record<string, string | number | boolean> }) {
  return (
    <dl className="definition-list">
      {Object.entries(values).map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <Card title={label}>
      <strong className="metric-value">{value}</strong>
    </Card>
  );
}
