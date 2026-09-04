import { useCallback, useEffect, useState } from "react";
import { ControlPlaneError } from "../api/client";
import type {
  CanonicalVerification,
  CanonicalVerificationRequirement,
  VerificationClient,
} from "../api/verification";
import { AppLink } from "../app/router";
import { CanonicalId, Card, EmptyState, ErrorState, LoadingState, StatusBadge } from "./States";

export type VerificationSummaryScope =
  | { kind: "task"; id: string }
  | { kind: "run"; id: string }
  | { kind: "result"; id: string };

export function VerificationSummary({
  client,
  scope,
}: {
  client: VerificationClient;
  scope: VerificationSummaryScope;
}) {
  const [verifications, setVerifications] = useState<CanonicalVerification[]>([]);
  const [requirement, setRequirement] = useState<CanonicalVerificationRequirement | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const pagePromise = client.list({
        limit: 50,
        sort: "created_at",
        direction: "desc",
        filters: { [`${scope.kind}_id`]: scope.id },
      });
      const requirementPromise =
        scope.kind === "task" ? optionalRequirement(client, scope.id) : Promise.resolve(null);
      const [page, nextRequirement] = await Promise.all([pagePromise, requirementPromise]);
      setVerifications(page.items);
      setRequirement(nextRequirement);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [client, scope.id, scope.kind]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <VerificationSummaryView
      error={error}
      loading={loading}
      onRetry={() => void load()}
      requirement={requirement}
      scope={scope}
      verifications={verifications}
    />
  );
}

export function VerificationSummaryView({
  error,
  loading,
  onRetry,
  requirement,
  scope,
  verifications,
}: {
  error: unknown;
  loading: boolean;
  onRetry?: () => void;
  requirement: CanonicalVerificationRequirement | null;
  scope: VerificationSummaryScope;
  verifications: CanonicalVerification[];
}) {
  return (
    <Card title="Verification">
      {loading ? <LoadingState label="Loading verification status…" /> : null}
      {!loading && error ? <ErrorState error={error} onRetry={onRetry} /> : null}
      {!loading && !error && requirement ? <RequirementStatus requirement={requirement} /> : null}
      {!loading && !error && verifications.length > 0 ? (
        <VerificationHistory verifications={verifications} />
      ) : null}
      {!loading && !error && !requirement && verifications.length === 0 ? (
        <EmptyState
          title="No verification recorded"
          detail={`No canonical Verification is currently attached to this ${scope.kind}.`}
        />
      ) : null}
    </Card>
  );
}

function RequirementStatus({
  requirement,
}: {
  requirement: CanonicalVerificationRequirement;
}) {
  return (
    <div className="stack">
      <div className="detail-status">
        <StatusBadge value={requirement.completion.state} />
        <span>
          Policy <CanonicalId value={requirement.policy.id} /> v{requirement.policy.version}
        </span>
      </div>
      <dl>
        <div>
          <dt>Completion reason</dt>
          <dd>{requirement.completion.reason}</dd>
        </div>
        <div>
          <dt>Repair attempts remaining</dt>
          <dd>{requirement.completion.repair_attempts_remaining}</dd>
        </div>
        {requirement.subject ? (
          <>
            <div>
              <dt>Bound subject</dt>
              <dd>
                {requirement.subject.type}:<CanonicalId value={requirement.subject.id} /> revision {requirement.subject.revision}
              </dd>
            </div>
            <div>
              <dt>Digest</dt>
              <dd><code>{requirement.subject.digest}</code></dd>
            </div>
          </>
        ) : null}
      </dl>
    </div>
  );
}

function VerificationHistory({
  verifications,
}: {
  verifications: CanonicalVerification[];
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Verification</th>
            <th>Policy</th>
            <th>Status</th>
            <th>Outcome</th>
            <th>Subject</th>
          </tr>
        </thead>
        <tbody>
          {verifications.map((verification) => (
            <tr key={verification.id}>
              <td>
                <AppLink href={`/verification/${verification.id}`}>
                  <CanonicalId value={verification.id} />
                </AppLink>
              </td>
              <td>
                <CanonicalId value={verification.policy.id} /> v{verification.policy.version}
              </td>
              <td><StatusBadge value={verification.status} /></td>
              <td>
                {verification.verification_result ? (
                  <StatusBadge value={verification.verification_result.outcome} />
                ) : (
                  "—"
                )}
              </td>
              <td>
                {verification.subject.type}:<CanonicalId value={verification.subject.id} /> r{verification.subject.revision}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

async function optionalRequirement(
  client: VerificationClient,
  taskId: string,
): Promise<CanonicalVerificationRequirement | null> {
  try {
    return await client.getRequirement(taskId);
  } catch (error) {
    if (error instanceof ControlPlaneError && error.status === 404) return null;
    throw error;
  }
}
