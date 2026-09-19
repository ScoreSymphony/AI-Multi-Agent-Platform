import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import type { CanonicalDecisionRecord, DecisionReference } from "../api/decisions";
import { DecisionRecordClient } from "../api/decisions";
import type {
  CanonicalResearchClaim,
  CanonicalResearchEvidence,
  CanonicalResearchItem,
  CanonicalResearchObservation,
  CanonicalResearchSource,
} from "../api/research";
import { ResearchClient } from "../api/research";
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

export const RESEARCH_ROUTE = "/research";

interface ResearchFilters {
  q?: string;
  researchClass?: string;
  status?: string;
}

export function ResearchPage({ client }: { client: ResearchClient }) {
  const { search, navigate } = useRouter();
  const filters = useMemo(() => researchFiltersFromQuery(search), [search]);
  const queryKey = useMemo(() => JSON.stringify(filters), [filters]);
  const pagination = useCursorPagination(queryKey);
  const [page, setPage] = useState<Page<CanonicalResearchItem> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const query: ListQuery = {
        limit: 50,
        cursor: pagination.cursor,
        sort: "updated_at",
        direction: "desc",
        q: filters.q,
        filters: {
          ...(filters.researchClass ? { research_class: filters.researchClass } : {}),
          ...(filters.status ? { status: filters.status } : {}),
        },
      };
      setPage(await client.listItems(query));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [client, filters.q, filters.researchClass, filters.status, pagination.cursor]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    pagination.reset();
    navigate(researchFiltersToPath(new FormData(event.currentTarget)));
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Evidence-backed investigation</p>
        <h1>Research Evidence</h1>
        <p>
          Inspect canonical Research Items and their exact Sources, Claims, Evidence,
          freshness and Verification bindings. Research remains evidence metadata;
          Tasks, Runs, Verification and Decisions retain their own authority.
        </p>
      </header>

      <Card title="Research filters">
        <form className="form-grid" onSubmit={submit}>
          <label>
            Query
            <input name="q" defaultValue={filters.q ?? ""} placeholder="title, question, ID or evidence" />
          </label>
          <label>
            Research class
            <select name="research_class" defaultValue={filters.researchClass ?? ""}>
              <option value="">All</option>
              <option value="task_research">task_research</option>
              <option value="project_research">project_research</option>
              <option value="domain_research">domain_research</option>
            </select>
          </label>
          <label>
            Status
            <input name="status" defaultValue={filters.status ?? ""} placeholder="current status" />
          </label>
          <div className="actions">
            <button className="primary" type="submit">Apply</button>
            <button type="button" onClick={() => navigate(RESEARCH_ROUTE)}>Reset</button>
          </div>
        </form>
      </Card>

      <Card title="Research Items">
        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
        {loading && !page ? <LoadingState /> : null}
        {loading && page ? <p role="status">Refreshing Research Items…</p> : null}
        {page ? <ResearchItemTable items={page.items} /> : null}
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

export function ResearchDetailPage({
  client,
  decisions,
  researchItemId,
}: {
  client: ResearchClient;
  decisions: DecisionRecordClient;
  researchItemId: string;
}) {
  const [item, setItem] = useState<CanonicalResearchItem | null>(null);
  const [sources, setSources] = useState<CanonicalResearchSource[]>([]);
  const [observations, setObservations] = useState<CanonicalResearchObservation[]>([]);
  const [claims, setClaims] = useState<CanonicalResearchClaim[]>([]);
  const [evidence, setEvidence] = useState<CanonicalResearchEvidence[]>([]);
  const [linkedDecisions, setLinkedDecisions] = useState<CanonicalDecisionRecord[]>([]);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const current = await client.getItem(researchItemId);
      const [nextSources, nextClaims, nextEvidence] = await Promise.all([
        Promise.all(current.source_ids.map((id) => client.getSource(id))),
        Promise.all(current.claim_ids.map((id) => client.getClaim(id))),
        Promise.all(current.evidence_ids.map((id) => client.getEvidence(id))),
      ]);
      const observationIds = [...new Set(nextSources.flatMap((source) => source.observation_ids))];
      const [nextObservations, decisionInventory] = await Promise.all([
        Promise.all(observationIds.map((id) => client.getObservation(id))),
        listAllDecisions(decisions),
      ]);
      const researchIds = new Set([
        current.id,
        ...current.claim_ids,
        ...current.evidence_ids,
      ]);
      setItem(current);
      setSources(nextSources);
      setClaims(nextClaims);
      setEvidence(nextEvidence);
      setObservations(nextObservations);
      setLinkedDecisions(
        decisionInventory.filter((decision) => decisionReferencesAny(decision, researchIds)),
      );
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, decisions, researchItemId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !item) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!item) return <LoadingState label="Loading Research Item…" />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Research Item</p>
          <h1>{item.title}</h1>
          <CanonicalId value={item.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={item.status} />
          <span>{item.research_class}</span>
        </div>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      <div className="grid-two">
        <Card title="Question & scope">
          <p>{item.question}</p>
          <dl>
            <dt>revision</dt><dd>{item.revision}</dd>
            <dt>digest</dt><dd><code>{item.digest}</code></dd>
            <dt>owner</dt><dd>{item.owner_type}:{item.owner_id}</dd>
            <dt>data class</dt><dd>{item.data_class}</dd>
            <dt>updated</dt><dd>{formatDate(item.updated_at)}</dd>
          </dl>
        </Card>
        <Card title="Canonical workflow provenance">
          <ReferenceRow label="Project" id={item.project_id} path="projects" />
          <ReferenceRow label="Workspace" id={item.workspace_id} path="workspaces" />
          <ReferenceRow label="Task" id={item.task_id} path="tasks" />
          <ReferenceRow label="Plan" id={item.plan_id} path="plans" />
          <ReferenceRow label="Run" id={item.run_id} path="runs" />
          {!item.project_id && !item.workspace_id && !item.task_id && !item.plan_id && !item.run_id
            ? <EmptyState title="No originating workflow reference" />
            : null}
        </Card>
      </div>

      <Card title="Sources & observations">
        {sources.length ? (
          <div className="stack">
            {sources.map((source) => (
              <section key={source.id}>
                <h3>{source.title}</h3>
                <p><CanonicalId value={source.id} /> · {source.source_type} · {source.trust_classification}</p>
                <p>{source.locator}</p>
                <p>Current state: <strong>{source.current_observation_state ?? "unobserved"}</strong></p>
                <ul className="reference-list">
                  {observations.filter((observation) => observation.source_id === source.id).map((observation) => (
                    <li key={observation.id}>
                      <CanonicalId value={observation.id} /> · <StatusBadge value={observation.state} />
                      {" · "}{formatDate(observation.retrieved_at)}
                      {observation.commit ? <> · commit <code>{observation.commit}</code></> : null}
                      {observation.snapshot_artifact_id ? (
                        <> · <AppLink href={`/artifacts/${encodeURIComponent(observation.snapshot_artifact_id)}`}>snapshot artifact</AppLink></>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        ) : <EmptyState title="No Sources" detail="This Research Item has no canonical Source records." />}
      </Card>

      <Card title="Claims">
        {claims.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Claim</th><th>Status</th><th>Confidence</th><th>Evidence</th></tr></thead>
              <tbody>
                {claims.map((claim) => (
                  <tr key={claim.id}>
                    <td><strong>{claim.text}</strong><div><CanonicalId value={claim.id} /></div><small>{claim.category}</small></td>
                    <td><StatusBadge value={claim.status} /></td>
                    <td>{claim.confidence}</td>
                    <td>{claim.evidence_ids.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState title="No Claims" />}
      </Card>

      <Card title="Evidence & freshness">
        {evidence.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Evidence</th><th>Relation</th><th>Freshness</th><th>Source binding</th><th>Provenance</th></tr></thead>
              <tbody>
                {evidence.map((record) => (
                  <tr key={record.id}>
                    <td><CanonicalId value={record.id} /><div>Claim <CanonicalId value={record.claim_id} /></div></td>
                    <td><StatusBadge value={record.relation} /></td>
                    <td><StatusBadge value={record.freshness} /></td>
                    <td>
                      <CanonicalId value={record.source_observation_id} />
                      {record.source_commit ? <div><code>{record.source_commit}</code></div> : null}
                    </td>
                    <td>
                      {record.task_id ? <AppLink href={`/tasks/${encodeURIComponent(record.task_id)}`}>Task</AppLink> : null}
                      {record.run_id ? <> · <AppLink href={`/runs/${encodeURIComponent(record.run_id)}`}>Run</AppLink></> : null}
                      {record.artifact_id ? <> · <AppLink href={`/artifacts/${encodeURIComponent(record.artifact_id)}`}>Artifact</AppLink></> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState title="No Evidence" />}
      </Card>

      <Card title="Verification bindings">
        {item.verification_bindings.length ? (
          <ul className="reference-list">
            {item.verification_bindings.map((binding) => (
              <li key={binding.binding_id}>
                <AppLink href={`/verification/${encodeURIComponent(binding.verification_id)}`}>
                  Verification <CanonicalId value={binding.verification_id} />
                </AppLink>
                {" · "}{binding.subject_type} <CanonicalId value={binding.subject_id} />
                {" · revision "}{binding.subject_revision}
              </li>
            ))}
          </ul>
        ) : <EmptyState title="No Verification bindings" />}
      </Card>

      <Card title="Downstream Decision Records">
        {linkedDecisions.length ? (
          <ul className="reference-list">
            {linkedDecisions.map((decision) => (
              <li key={decision.id}>
                <AppLink href={`/decisions/${encodeURIComponent(decision.id)}`}>{decision.title}</AppLink>
                {" · "}<StatusBadge value={decision.status} />{" · "}{decision.outcome}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            title="No linked Decision Records"
            detail="No visible canonical Decision Record references this Research Item, its Claims or its Evidence."
          />
        )}
      </Card>

      <div className="actions">
        <AppLink href={RESEARCH_ROUTE}>Back to Research Evidence</AppLink>
        <button onClick={() => void load()}>Refresh canonical state</button>
      </div>
    </div>
  );
}

function ResearchItemTable({ items }: { items: CanonicalResearchItem[] }) {
  if (!items.length) return <EmptyState title="No Research Items" detail="No authorized Research Items match these filters." />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Research Item</th><th>Class</th><th>Status</th><th>Evidence graph</th><th>Updated</th></tr></thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>
                <strong><AppLink href={`/research/${encodeURIComponent(item.id)}`}>{item.title}</AppLink></strong>
                <div><CanonicalId value={item.id} /></div>
                <small>{item.question}</small>
              </td>
              <td>{item.research_class}</td>
              <td><StatusBadge value={item.status} /></td>
              <td>{item.source_ids.length} sources · {item.claim_ids.length} claims · {item.evidence_ids.length} evidence</td>
              <td>{formatDate(item.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReferenceRow({ label, id, path }: { label: string; id: string | null; path: string }) {
  if (!id) return null;
  return <p>{label}: <AppLink href={`/${path}/${encodeURIComponent(id)}`}><CanonicalId value={id} /></AppLink></p>;
}

async function listAllDecisions(client: DecisionRecordClient): Promise<CanonicalDecisionRecord[]> {
  const items: CanonicalDecisionRecord[] = [];
  let cursor: string | undefined;
  const seen = new Set<string>();
  while (true) {
    const page = await client.list({ limit: 200, cursor, sort: "id", direction: "asc" });
    items.push(...page.items);
    const next = page.next_cursor ?? undefined;
    if (!next) return items;
    if (seen.has(next)) throw new Error("Decision Record pagination returned a repeated cursor.");
    seen.add(next);
    cursor = next;
  }
}

function decisionReferencesAny(decision: CanonicalDecisionRecord, ids: Set<string>): boolean {
  const refs: Array<DecisionReference | null> = [
    decision.subject_ref,
    decision.approval_ref,
    decision.adr_ref,
    ...decision.evidence_refs,
    ...decision.evaluation_refs,
    ...decision.finding_refs,
    ...decision.cost_resource_refs,
    ...decision.downstream_refs,
    ...decision.alternatives.flatMap((alternative) => [
      alternative.resource_ref,
      ...alternative.evidence_refs,
    ]),
  ];
  return refs.some((reference) => reference !== null && ids.has(reference.resource_id));
}

function researchFiltersFromQuery(search: string): ResearchFilters {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  return {
    q: clean(params.get("q")),
    researchClass: clean(params.get("research_class")),
    status: clean(params.get("status")),
  };
}

function researchFiltersToPath(data: FormData): string {
  const params = new URLSearchParams();
  for (const key of ["q", "research_class", "status"]) {
    const value = clean(String(data.get(key) ?? ""));
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return query ? `${RESEARCH_ROUTE}?${query}` : RESEARCH_ROUTE;
}

function clean(value: string | null): string | undefined {
  const next = value?.trim();
  return next ? next : undefined;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
