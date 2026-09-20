import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import type {
  CanonicalDecisionRecord,
  DecisionAlternative,
  DecisionRecordClient,
  DecisionReference,
} from "../api/decisions";
import type { ListQuery, Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink, useRouter } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  CanonicalId,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

export const DECISIONS_ROUTE = "/decisions";

interface DecisionFilters {
  q?: string;
  category?: string;
  scopeType?: string;
  status?: string;
}

export function DecisionRecordsPage({
  client,
  commands = [],
}: {
  client: DecisionRecordClient;
  commands?: readonly string[];
}) {
  const { search, navigate } = useRouter();
  const filters = useMemo(() => decisionFiltersFromQuery(search), [search]);
  const queryKey = useMemo(() => JSON.stringify(filters), [filters]);
  const pagination = useCursorPagination(queryKey);
  const [page, setPage] = useState<Page<CanonicalDecisionRecord> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const commandSet = useMemo(() => new Set(commands), [commands]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const query: ListQuery = {
        limit: 50,
        cursor: pagination.cursor,
        sort: "created_at",
        direction: "desc",
        q: filters.q,
        filters: {
          ...(filters.category ? { category: filters.category } : {}),
          ...(filters.scopeType ? { scope_type: filters.scopeType } : {}),
          ...(filters.status ? { status: filters.status } : {}),
        },
      };
      setPage(await client.list(query));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [client, filters.category, filters.q, filters.scopeType, filters.status, pagination.cursor]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    pagination.reset();
    navigate(decisionFiltersToPath(new FormData(event.currentTarget)));
  };

  const createDecision = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setCreating(true);
    setActionError(null);
    try {
      const evidenceKind = optionalFormValue(form, "evidence_kind");
      const evidenceId = optionalFormValue(form, "evidence_id");
      if ((evidenceKind === undefined) !== (evidenceId === undefined)) {
        throw new Error("Evidence kind and Evidence ID must be supplied together.");
      }
      const outcome = requiredFormValue(form, "outcome");
      const created = await client.create({
        title: requiredFormValue(form, "title"),
        subject: requiredFormValue(form, "subject"),
        category: requiredFormValue(form, "category"),
        scope_type: requiredFormValue(form, "scope_type"),
        scope_id: optionalFormValue(form, "scope_id") ?? null,
        question: requiredFormValue(form, "question"),
        alternatives: [{
          label: requiredFormValue(form, "alternative"),
          status: ["adopt", "experimental", "custom"].includes(outcome) ? "selected" : "considered",
          resource_ref: null,
          evidence_refs: [],
          trade_offs: [],
          unknowns: [],
        }],
        outcome,
        rationale: requiredFormValue(form, "rationale"),
        evidence_refs: evidenceKind && evidenceId
          ? [{ kind: evidenceKind, resource_id: evidenceId, revision: null, digest: null, locator: null, metadata: {} }]
          : [],
        evaluation_refs: [],
        finding_refs: [],
        cost_resource_refs: [],
        reviewer_refs: [],
        approval_ref: null,
        adr_ref: null,
      });
      navigate(`/decisions/${encodeURIComponent(created.id)}`);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Evidence-backed governance history</p>
        <h1>Decision Records</h1>
        <p>
          Inspect immutable canonical decisions, alternatives, exact evidence references,
          supersession history and review state. A Decision Record never grants permission
          or activates its subject by itself.
        </p>
      </header>

      {commandSet.has("decision-record.create") ? (
        <Card title="Create Decision Record">
          {actionError ? <ErrorState error={actionError} /> : null}
          <form className="form-grid" onSubmit={(event) => void createDecision(event)}>
            <label>Title<input name="title" required /></label>
            <label>Subject<input name="subject" required /></label>
            <label>Category<input name="category" required /></label>
            <label>Scope type<input name="scope_type" defaultValue="platform" required /></label>
            <label>Scope ID<input name="scope_id" placeholder="optional" /></label>
            <label>Question<textarea name="question" required /></label>
            <label>Considered alternative<input name="alternative" required /></label>
            <label>
              Outcome
              <select name="outcome" defaultValue="adopt">
                {["adopt","reject","defer","experimental","custom"].map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
            <label>Rationale<textarea name="rationale" required /></label>
            <label>Evidence kind<input name="evidence_kind" placeholder="optional, e.g. research-evidence" /></label>
            <label>Evidence ID<input name="evidence_id" placeholder="optional exact canonical ID" /></label>
            <button className="primary" disabled={creating}>
              {creating ? "Creating…" : "Create Decision Record"}
            </button>
          </form>
        </Card>
      ) : null}

      <Card title="Decision filters">
        <form className="form-grid" onSubmit={submit}>
          <label>
            Query
            <input name="q" defaultValue={filters.q ?? ""} placeholder="title, subject, rationale or ID" />
          </label>
          <label>
            Category
            <input name="category" defaultValue={filters.category ?? ""} placeholder="provider/tool/plugin…" />
          </label>
          <label>
            Scope type
            <input name="scope_type" defaultValue={filters.scopeType ?? ""} placeholder="platform/project/org…" />
          </label>
          <label>
            Status
            <select name="status" defaultValue={filters.status ?? ""}>
              <option value="">All</option>
              <option value="current">current</option>
              <option value="superseded">superseded</option>
              <option value="withdrawn">withdrawn</option>
            </select>
          </label>
          <div className="actions">
            <button className="primary" type="submit">Apply</button>
            <button type="button" onClick={() => navigate(DECISIONS_ROUTE)}>Reset</button>
          </div>
        </form>
      </Card>

      <Card title="Decision history">
        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
        {loading && !page ? <LoadingState /> : null}
        {loading && page ? <p role="status">Refreshing Decision Records…</p> : null}
        {page ? <DecisionTable decisions={page.items} /> : null}
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

export function DecisionRecordDetailPage({
  client,
  decisionRecordId,
  commands = [],
}: {
  client: DecisionRecordClient;
  decisionRecordId: string;
  commands?: readonly string[];
}) {
  const [decision, setDecision] = useState<CanonicalDecisionRecord | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const commandSet = useMemo(() => new Set(commands), [commands]);

  const load = useCallback(async () => {
    try {
      setDecision(await client.get(decisionRecordId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, decisionRecordId]);

  useEffect(() => {
    void load();
  }, [load]);

  const supersede = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!decision) return;
    const form = new FormData(event.currentTarget);
    setBusyAction("supersede");
    setActionError(null);
    try {
      const replacement = await client.supersede(
        decision.id,
        decisionMutationPayload(decision, {
          title: requiredFormValue(form, "title"),
          question: requiredFormValue(form, "question"),
          rationale: requiredFormValue(form, "rationale"),
        }),
      );
      setDecision(replacement);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusyAction(null);
    }
  };

  const withdraw = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!decision) return;
    const form = new FormData(event.currentTarget);
    const reason = requiredFormValue(form, "reason");
    if (!window.confirm(`Withdraw Decision Record "${decision.title}"? This appends a canonical withdrawal relation.`)) {
      return;
    }
    setBusyAction("withdraw");
    setActionError(null);
    try {
      setDecision(await client.withdraw(decision.id, reason));
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusyAction(null);
    }
  };

  if (error && !decision) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!decision) return <LoadingState label="Loading Decision Record…" />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Decision Record</p>
          <h1>{decision.title}</h1>
          <CanonicalId value={decision.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={decision.status} />
          <StatusBadge value={decision.outcome} />
          {decision.revisit_due ? <StatusBadge value="revisit_due" /> : null}
        </div>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {actionError ? <ErrorState error={actionError} /> : null}

      {decision.status === "current" && (
        commandSet.has("decision-record.supersede") || commandSet.has("decision-record.withdraw")
      ) ? (
        <Card title="Decision lifecycle">
          <div className="grid-two">
            {commandSet.has("decision-record.supersede") ? (
              <form className="form-grid" onSubmit={(event) => void supersede(event)}>
                <h3>Supersede</h3>
                <label>Replacement title<input name="title" defaultValue={decision.title} required /></label>
                <label>Question<textarea name="question" defaultValue={decision.question} required /></label>
                <label>Rationale<textarea name="rationale" defaultValue={decision.rationale} required /></label>
                <button disabled={busyAction !== null}>
                  {busyAction === "supersede" ? "Superseding…" : "Create superseding Decision"}
                </button>
              </form>
            ) : null}
            {commandSet.has("decision-record.withdraw") ? (
              <form className="form-grid" onSubmit={(event) => void withdraw(event)}>
                <h3>Withdraw</h3>
                <label>Reason<textarea name="reason" required /></label>
                <button className="danger" disabled={busyAction !== null}>
                  {busyAction === "withdraw" ? "Withdrawing…" : "Withdraw Decision"}
                </button>
              </form>
            ) : null}
          </div>
        </Card>
      ) : null}

      <div className="grid-two">
        <Card title="Decision">
          <p><strong>{decision.question}</strong></p>
          <p>{decision.rationale}</p>
          <dl>
            <dt>subject</dt><dd>{decision.subject}</dd>
            <dt>category</dt><dd>{decision.category}</dd>
            <dt>scope</dt><dd>{decision.scope_type}{decision.scope_id ? `:${decision.scope_id}` : ""}</dd>
            <dt>actor</dt><dd>{decision.actor_ref}</dd>
            <dt>effective</dt><dd>{formatDate(decision.effective_at)}</dd>
            <dt>revision</dt><dd>{decision.revision}</dd>
            <dt>digest</dt><dd><code>{decision.content_digest}</code></dd>
          </dl>
        </Card>
        <Card title="Review & supersession">
          <p>Review at: {decision.review_at ? formatDate(decision.review_at) : "—"}</p>
          <p>Review condition: {decision.review_condition ?? "—"}</p>
          <p>Revisit due: <strong>{decision.revisit_due ? "yes" : "no"}</strong></p>
          {decision.supersedes ? (
            <p>Supersedes <AppLink href={`/decisions/${encodeURIComponent(decision.supersedes)}`}><CanonicalId value={decision.supersedes} /></AppLink></p>
          ) : null}
          {decision.superseded_by ? (
            <p>Superseded by <AppLink href={`/decisions/${encodeURIComponent(decision.superseded_by)}`}><CanonicalId value={decision.superseded_by} /></AppLink></p>
          ) : null}
          {decision.withdrawn_at ? <p>Withdrawn {formatDate(decision.withdrawn_at)} · {decision.withdrawal_reason ?? "no reason recorded"}</p> : null}
        </Card>
      </div>

      <Card title="Alternatives">
        {decision.alternatives.length
          ? <AlternativeTable alternatives={decision.alternatives} />
          : <EmptyState title="No alternatives recorded" />}
      </Card>

      <div className="grid-two">
        <ReferenceCard title="Research / evidence references" references={decision.evidence_refs} />
        <ReferenceCard title="Evaluation references" references={decision.evaluation_refs} />
        <ReferenceCard title="Findings" references={decision.finding_refs} />
        <ReferenceCard title="Cost / resource evidence" references={decision.cost_resource_refs} />
      </div>

      <div className="grid-two">
        <Card title="Approval / ADR context">
          {decision.approval_ref ? <ReferenceView reference={decision.approval_ref} /> : <p>Approval: —</p>}
          {decision.adr_ref ? <ReferenceView reference={decision.adr_ref} /> : <p>ADR: —</p>}
        </Card>
        <ReferenceCard title="Downstream provenance" references={decision.downstream_refs} />
      </div>

      <div className="actions">
        <AppLink href={DECISIONS_ROUTE}>Back to Decision Records</AppLink>
        <button onClick={() => void load()}>Refresh canonical state</button>
      </div>
    </div>
  );
}

function DecisionTable({ decisions }: { decisions: CanonicalDecisionRecord[] }) {
  if (!decisions.length) return <EmptyState title="No Decision Records" detail="No authorized decisions match these filters." />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Decision</th><th>Category / scope</th><th>Outcome</th><th>Status</th><th>Review</th></tr></thead>
        <tbody>
          {decisions.map((decision) => (
            <tr key={decision.id}>
              <td>
                <strong><AppLink href={`/decisions/${encodeURIComponent(decision.id)}`}>{decision.title}</AppLink></strong>
                <div><CanonicalId value={decision.id} /></div>
                <small>{decision.subject}</small>
              </td>
              <td>{decision.category}<div><small>{decision.scope_type}{decision.scope_id ? `:${decision.scope_id}` : ""}</small></div></td>
              <td><StatusBadge value={decision.outcome} /></td>
              <td><StatusBadge value={decision.status} /></td>
              <td>{decision.revisit_due ? <StatusBadge value="revisit_due" /> : decision.review_at ? formatDate(decision.review_at) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AlternativeTable({ alternatives }: { alternatives: DecisionAlternative[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Alternative</th><th>Status</th><th>Resource</th><th>Evidence</th><th>Trade-offs / unknowns</th></tr></thead>
        <tbody>
          {alternatives.map((alternative, index) => (
            <tr key={`${alternative.label}:${index}`}>
              <td><strong>{alternative.label}</strong></td>
              <td><StatusBadge value={alternative.status} /></td>
              <td>{alternative.resource_ref ? <ReferenceView reference={alternative.resource_ref} /> : "—"}</td>
              <td>{alternative.evidence_refs.length ? alternative.evidence_refs.map((reference) => <ReferenceView key={`${reference.kind}:${reference.resource_id}`} reference={reference} />) : "—"}</td>
              <td>
                {alternative.trade_offs.length ? <div>Trade-offs: {alternative.trade_offs.join("; ")}</div> : null}
                {alternative.unknowns.length ? <div>Unknowns: {alternative.unknowns.join("; ")}</div> : null}
                {!alternative.trade_offs.length && !alternative.unknowns.length ? "—" : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReferenceCard({ title, references }: { title: string; references: DecisionReference[] }) {
  return (
    <Card title={title}>
      {references.length
        ? <div className="stack">{references.map((reference) => <ReferenceView key={`${reference.kind}:${reference.resource_id}`} reference={reference} />)}</div>
        : <EmptyState title="No references" />}
    </Card>
  );
}

function ReferenceView({ reference }: { reference: DecisionReference }) {
  const path = referencePath(reference);
  const value = <><strong>{reference.kind}</strong> <CanonicalId value={reference.resource_id} /></>;
  return (
    <div>
      {path ? <AppLink href={path}>{value}</AppLink> : value}
      {reference.revision !== null ? <small> · revision {String(reference.revision)}</small> : null}
      {reference.digest ? <small> · digest <code>{reference.digest}</code></small> : null}
    </div>
  );
}

function referencePath(reference: DecisionReference): string | null {
  const id = encodeURIComponent(reference.resource_id);
  switch (reference.kind) {
    case "research-item":
    case "research_item":
      return `/research/${id}`;
    case "task":
      return `/tasks/${id}`;
    case "plan":
      return `/plans/${id}`;
    case "run":
      return `/runs/${id}`;
    case "artifact":
      return `/artifacts/${id}`;
    case "verification":
      return `/verification/${id}`;
    case "approval":
      return `/approvals/${id}`;
    default:
      return null;
  }
}

function decisionMutationPayload(
  decision: CanonicalDecisionRecord,
  changes: { title: string; question: string; rationale: string },
): Record<string, import("../api/types").JsonValue> {
  const reference = (value: DecisionReference | null) => value === null ? null : ({
    kind: value.kind,
    resource_id: value.resource_id,
    revision: value.revision,
    digest: value.digest,
    locator: value.locator,
    metadata: value.metadata,
  });
  return {
    title: changes.title,
    subject: decision.subject,
    category: decision.category,
    scope_type: decision.scope_type,
    scope_id: decision.scope_id,
    subject_ref: reference(decision.subject_ref),
    question: changes.question,
    alternatives: decision.alternatives.map((alternative) => ({
      label: alternative.label,
      status: alternative.status,
      resource_ref: reference(alternative.resource_ref),
      evidence_refs: alternative.evidence_refs.map(reference),
      trade_offs: alternative.trade_offs,
      unknowns: alternative.unknowns,
    })),
    outcome: decision.outcome,
    rationale: changes.rationale,
    evidence_refs: decision.evidence_refs.map(reference),
    evaluation_refs: decision.evaluation_refs.map(reference),
    finding_refs: decision.finding_refs.map(reference),
    cost_resource_refs: decision.cost_resource_refs.map(reference),
    reviewer_refs: decision.reviewer_refs,
    approval_ref: reference(decision.approval_ref),
    adr_ref: reference(decision.adr_ref),
    review_at: decision.review_at,
    review_condition: decision.review_condition,
  };
}

function requiredFormValue(form: FormData, key: string): string {
  const value = String(form.get(key) ?? "").trim();
  if (!value) throw new Error(`${key} is required`);
  return value;
}

function optionalFormValue(form: FormData, key: string): string | undefined {
  return clean(String(form.get(key) ?? ""));
}

function decisionFiltersFromQuery(search: string): DecisionFilters {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  return {
    q: clean(params.get("q")),
    category: clean(params.get("category")),
    scopeType: clean(params.get("scope_type")),
    status: clean(params.get("status")),
  };
}

function decisionFiltersToPath(data: FormData): string {
  const params = new URLSearchParams();
  for (const key of ["q", "category", "scope_type", "status"]) {
    const value = clean(String(data.get(key) ?? ""));
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return query ? `${DECISIONS_ROUTE}?${query}` : DECISIONS_ROUTE;
}

function clean(value: string | null): string | undefined {
  const next = value?.trim();
  return next ? next : undefined;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
