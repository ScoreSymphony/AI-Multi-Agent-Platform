import { useCallback, useEffect, useMemo, useState } from "react";
import {
  GovernanceClient,
  type GovernanceAuditEvent,
  type GovernanceProposal,
  type GovernanceSpecification,
} from "../api/governance";
import { AppLink } from "../app/router";
import {
  Card,
  CanonicalId,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

export function GovernancePage({ client }: { client: GovernanceClient }) {
  const [proposals, setProposals] = useState<GovernanceProposal[] | null>(null);
  const [specifications, setSpecifications] = useState<GovernanceSpecification[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [reason, setReason] = useState("");
  const [projectId, setProjectId] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [proposalPage, specificationPage] = await Promise.all([
        client.listProposals({ limit: 100, sort: "id", direction: "asc" }),
        client.listSpecifications({ limit: 100, sort: "id", direction: "asc" }),
      ]);
      setProposals(proposalPage.items);
      setSpecifications(specificationPage.items);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const createProposal = async () => {
    if (!title.trim() || !summary.trim() || !reason.trim()) return;
    setBusy(true);
    try {
      await client.createProposal({
        title: title.trim(),
        summary: summary.trim(),
        reason: reason.trim(),
        source: "web-governance",
        status: "proposed",
        ...(projectId.trim() ? { project_id: projectId.trim() } : {}),
      });
      setTitle("");
      setSummary("");
      setReason("");
      await load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Optional pre-execution governance</p>
        <h1>Proposals & Specifications</h1>
        <p>
          Frame ambiguous or high-governance work before it becomes a canonical Task. Proposal and
          Specification state stays separate from Task, Plan, Step and Run lifecycle state.
        </p>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      <Card title="Create Proposal">
        <div className="stack">
          <label>
            Title
            <input value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label>
            Summary
            <textarea value={summary} onChange={(event) => setSummary(event.target.value)} />
          </label>
          <label>
            Reason / problem / opportunity
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
          <label>
            Project ID <small>(optional)</small>
            <input value={projectId} onChange={(event) => setProjectId(event.target.value)} />
          </label>
          <div className="actions">
            <button
              disabled={busy || !title.trim() || !summary.trim() || !reason.trim()}
              onClick={() => void createProposal()}
            >
              Create Proposal
            </button>
            <button disabled={busy} onClick={() => void load()}>Refresh</button>
          </div>
        </div>
      </Card>

      <Card title="Proposal inbox">
        {proposals === null && !error ? <LoadingState /> : null}
        {proposals?.length === 0 ? (
          <EmptyState title="No Proposals" detail="Direct Task creation remains available." />
        ) : null}
        {proposals && proposals.length > 0 ? <ProposalTable proposals={proposals} /> : null}
      </Card>

      <Card title="Specifications">
        {specifications === null && !error ? <LoadingState /> : null}
        {specifications?.length === 0 ? (
          <EmptyState title="No Specifications" detail="Create one from a Proposal when review is useful." />
        ) : null}
        {specifications && specifications.length > 0 ? (
          <SpecificationTable specifications={specifications} />
        ) : null}
      </Card>
    </div>
  );
}

export function ProposalGovernanceDetailPage({
  client,
  proposalId,
}: {
  client: GovernanceClient;
  proposalId: string;
}) {
  const [proposal, setProposal] = useState<GovernanceProposal | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [summary, setSummary] = useState("");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState("");
  const [goal, setGoal] = useState("");
  const [scope, setScope] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [constraints, setConstraints] = useState("");
  const [humanGates, setHumanGates] = useState("");
  const [risk, setRisk] = useState("standard");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const loaded = await client.getProposal(proposalId);
      setProposal(loaded);
      setSummary(loaded.summary);
      setReason(loaded.reason);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, proposalId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && proposal === null) return <ErrorState error={error} onRetry={() => void load()} />;
  if (proposal === null) return <LoadingState />;

  const revise = async () => {
    setBusy(true);
    try {
      const updated = await client.reviseProposal(proposal.id, proposal.revision, {
        summary: summary.trim(),
        reason: reason.trim(),
      });
      setProposal(updated);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const requestClarification = async () => {
    setBusy(true);
    try {
      setProposal(await client.requestClarification(proposal.id, proposal.revision));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const dismiss = async () => {
    setBusy(true);
    try {
      setProposal(await client.dismissProposal(proposal.id, proposal.revision));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const createSpecification = async () => {
    if (!problem.trim() || !goal.trim() || splitLines(scope).length === 0 || splitLines(acceptance).length === 0) {
      return;
    }
    setBusy(true);
    try {
      const created = await client.createSpecification({
        proposal_id: proposal.id,
        ...(proposal.project_id ? { project_id: proposal.project_id } : {}),
        ...(proposal.workspace_id ? { workspace_id: proposal.workspace_id } : {}),
        problem: problem.trim(),
        goal: goal.trim(),
        scope: splitLines(scope),
        acceptance_criteria: splitLines(acceptance),
        constraints: splitLines(constraints),
        required_human_gates: splitLines(humanGates),
        risk,
      });
      window.history.pushState({}, "", `/governance/specifications/${encodeURIComponent(created.id)}`);
      window.dispatchEvent(new PopStateEvent("popstate"));
    } catch (nextError) {
      setError(nextError);
      setBusy(false);
    }
  };

  const terminal = ["dismissed", "superseded", "converted_to_task"].includes(proposal.status);
  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Proposal</p>
        <h1>{proposal.title}</h1>
        <p><CanonicalId value={proposal.id} /> · <StatusBadge value={proposal.status} /></p>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      <Card title="Proposal contract">
        <dl className="detail-list">
          <dt>Revision</dt><dd>{proposal.revision}</dd>
          <dt>Risk</dt><dd><StatusBadge value={proposal.risk} /></dd>
          <dt>Source</dt><dd>{proposal.source}</dd>
          <dt>Project</dt><dd>{proposal.project_id ? <CanonicalId value={proposal.project_id} /> : "—"}</dd>
          <dt>Resulting Task</dt>
          <dd>{proposal.converted_task_id ? <AppLink href={`/tasks/${proposal.converted_task_id}`}><CanonicalId value={proposal.converted_task_id} /></AppLink> : "—"}</dd>
        </dl>
        <label>
          Summary
          <textarea disabled={terminal} value={summary} onChange={(event) => setSummary(event.target.value)} />
        </label>
        <label>
          Reason
          <textarea disabled={terminal} value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
        <div className="actions">
          <button disabled={busy || terminal} onClick={() => void revise()}>Save revision</button>
          <button disabled={busy || terminal} onClick={() => void requestClarification()}>Request clarification</button>
          <button disabled={busy || terminal} onClick={() => void dismiss()}>Dismiss</button>
        </div>
      </Card>

      {!terminal ? (
        <Card title="Create Specification">
          <div className="stack">
            <label>Problem<textarea value={problem} onChange={(event) => setProblem(event.target.value)} /></label>
            <label>Goal<textarea value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
            <LineListInput label="Scope" value={scope} onChange={setScope} />
            <LineListInput label="Acceptance criteria" value={acceptance} onChange={setAcceptance} />
            <LineListInput label="Constraints" value={constraints} onChange={setConstraints} />
            <LineListInput label="Required human gates" value={humanGates} onChange={setHumanGates} />
            <label>
              Risk
              <select value={risk} onChange={(event) => setRisk(event.target.value)}>
                <option value="standard">standard</option>
                <option value="elevated">elevated</option>
                <option value="high">high</option>
                <option value="critical">critical</option>
              </select>
            </label>
            <button disabled={busy} onClick={() => void createSpecification()}>Create Specification</button>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

export function SpecificationGovernanceDetailPage({
  client,
  specificationId,
}: {
  client: GovernanceClient;
  specificationId: string;
}) {
  const [specification, setSpecification] = useState<GovernanceSpecification | null>(null);
  const [revisions, setRevisions] = useState<GovernanceSpecification[]>([]);
  const [events, setEvents] = useState<GovernanceAuditEvent[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [selectedRevision, setSelectedRevision] = useState<number | null>(null);
  const [goal, setGoal] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [constraints, setConstraints] = useState("");

  const load = useCallback(async () => {
    try {
      const [current, revisionPage, auditPage] = await Promise.all([
        client.getSpecification(specificationId),
        client.listSpecificationRevisions({ limit: 200 }),
        client.listAuditEvents({ limit: 200 }),
      ]);
      const ownRevisions = revisionPage.items
        .filter((item) => item.specification_id === specificationId)
        .sort((left, right) => left.revision - right.revision);
      setSpecification(current);
      setRevisions(ownRevisions);
      setEvents(auditPage.items.filter((event) => event.resource_id === specificationId));
      setGoal(current.title);
      setAcceptance(current.acceptance_criteria.join("\n"));
      setConstraints(current.constraints.join("\n"));
      setSelectedRevision((value) => value ?? Math.max(1, current.revision - 1));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, specificationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const latestApproval = useMemo(() => {
    if (!specification) return null;
    return [...events].reverse().find((event) =>
      event.event_type === "specification.approval-requested"
      && event.revision === specification.revision
      && event.digest === specification.content_digest,
    ) ?? null;
  }, [events, specification]);

  const convertedEvent = useMemo(
    () => [...events].reverse().find((event) => event.event_type === "specification.converted-to-task") ?? null,
    [events],
  );

  const comparedRevision = revisions.find((revision) => revision.revision === selectedRevision) ?? null;
  const changedFields = specification && comparedRevision
    ? specificationChangedFields(comparedRevision, specification)
    : [];

  if (error && specification === null) return <ErrorState error={error} onRetry={() => void load()} />;
  if (specification === null) return <LoadingState />;

  const revise = async () => {
    setBusy(true);
    try {
      await client.reviseSpecification(specification.id, specification.revision, {
        goal: goal.trim(),
        acceptance_criteria: splitLines(acceptance),
        constraints: splitLines(constraints),
      });
      await load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const requestApproval = async () => {
    setBusy(true);
    try {
      await client.requestApproval(specification.id);
      await load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const convert = async () => {
    setBusy(true);
    try {
      const approvalId = stringMetadata(latestApproval?.metadata.approval_id);
      await client.convertToTask(specification.id, approvalId ?? undefined);
      await load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const approvalId = stringMetadata(latestApproval?.metadata.approval_id);
  const taskId = stringMetadata(convertedEvent?.metadata.task_id);

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Specification</p>
        <h1>{specification.title}</h1>
        <p><CanonicalId value={specification.id} /> · revision {specification.revision}</p>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      <Card title="Review contract">
        <dl className="detail-list">
          <dt>Content digest</dt><dd><code>{specification.content_digest}</code></dd>
          <dt>Risk</dt><dd><StatusBadge value={specification.risk} /></dd>
          <dt>Approval required</dt><dd>{specification.approval_required ? "yes" : "no"}</dd>
          <dt>Approval</dt>
          <dd>{approvalId ? <AppLink href={`/approvals/${approvalId}`}><CanonicalId value={approvalId} /></AppLink> : "—"}</dd>
          <dt>Resulting Task</dt>
          <dd>{taskId ? <AppLink href={`/tasks/${taskId}`}><CanonicalId value={taskId} /></AppLink> : "—"}</dd>
        </dl>
        <p><strong>Problem:</strong> {specification.problem}</p>
        <List label="Scope" values={specification.scope} />
        <List label="Required tests" values={specification.required_tests} />
        <List label="Verification" values={specification.verification_requirements} />
        <List label="Human gates" values={specification.required_human_gates} />
      </Card>

      <Card title="Revise Specification">
        <p>Any material change creates a new immutable revision and therefore a new content digest.</p>
        <label>Goal<textarea value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
        <LineListInput label="Acceptance criteria" value={acceptance} onChange={setAcceptance} />
        <LineListInput label="Constraints" value={constraints} onChange={setConstraints} />
        <div className="actions">
          <button disabled={busy} onClick={() => void revise()}>Create revision</button>
          <button disabled={busy} onClick={() => void requestApproval()}>Request Approval</button>
          <button disabled={busy} onClick={() => void convert()}>Convert to Task</button>
        </div>
      </Card>

      <Card title="Revision comparison">
        {revisions.length < 2 ? (
          <EmptyState title="Only one revision" detail="A comparison appears after the first revision." />
        ) : (
          <>
            <label>
              Compare current revision {specification.revision} with
              <select
                value={selectedRevision ?? ""}
                onChange={(event) => setSelectedRevision(Number(event.target.value))}
              >
                {revisions.filter((revision) => revision.revision !== specification.revision).map((revision) => (
                  <option key={revision.revision} value={revision.revision}>revision {revision.revision}</option>
                ))}
              </select>
            </label>
            {comparedRevision ? (
              <p>
                Changed fields: {changedFields.length > 0 ? changedFields.join(", ") : "none"}.
                Reviewed digest: <code>{comparedRevision.content_digest}</code>
              </p>
            ) : null}
          </>
        )}
      </Card>

      <Card title="Governance audit">
        {events.length === 0 ? <EmptyState title="No events" /> : (
          <ul>
            {events.map((event) => (
              <li key={event.id}>
                <strong>{event.event_type}</strong> · revision {event.revision ?? "—"} · {event.occurred_at}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function ProposalTable({ proposals }: { proposals: GovernanceProposal[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Proposal</th><th>Status</th><th>Risk</th><th>Revision</th></tr></thead>
        <tbody>
          {proposals.map((proposal) => (
            <tr key={proposal.id}>
              <td><AppLink href={`/governance/proposals/${proposal.id}`}>{proposal.title}</AppLink><br /><CanonicalId value={proposal.id} /></td>
              <td><StatusBadge value={proposal.status} /></td>
              <td><StatusBadge value={proposal.risk} /></td>
              <td>{proposal.revision}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SpecificationTable({ specifications }: { specifications: GovernanceSpecification[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Specification</th><th>Risk</th><th>Revision</th><th>Approval</th></tr></thead>
        <tbody>
          {specifications.map((specification) => (
            <tr key={specification.id}>
              <td><AppLink href={`/governance/specifications/${specification.id}`}>{specification.title}</AppLink><br /><CanonicalId value={specification.id} /></td>
              <td><StatusBadge value={specification.risk} /></td>
              <td>{specification.revision}</td>
              <td>{specification.approval_required ? "required" : "policy optional"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LineListInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label>
      {label} <small>(one item per line)</small>
      <textarea value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function List({ label, values }: { label: string; values: string[] }) {
  return (
    <div>
      <strong>{label}</strong>
      {values.length === 0 ? <p>—</p> : <ul>{values.map((value) => <li key={value}>{value}</li>)}</ul>}
    </div>
  );
}

function splitLines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function stringMetadata(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function specificationChangedFields(
  previous: GovernanceSpecification,
  current: GovernanceSpecification,
): string[] {
  const fields: Array<keyof GovernanceSpecification> = [
    "title",
    "problem",
    "scope",
    "out_of_scope",
    "acceptance_criteria",
    "dependencies",
    "constraints",
    "risk",
    "required_capabilities",
    "model_requirements",
    "agent_requirements",
    "data_security_constraints",
    "validation_strategy",
    "required_tests",
    "verification_requirements",
    "required_human_gates",
    "decomposition_hints",
    "assumptions",
    "open_questions",
  ];
  return fields.filter((field) => JSON.stringify(previous[field]) !== JSON.stringify(current[field]));
}
