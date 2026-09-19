import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { ControlPlaneClient, isControlPlaneError } from "../api/client";
import type {
  SearchMode,
  SearchPage as SearchPageResult,
  SearchRequest,
  SearchResult,
  SearchSort,
} from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink, useRouter } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  CanonicalId,
  Card,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const DEFAULT_SEARCH: SearchRequest = {
  limit: 25,
  sort: "updated_at",
  direction: "desc",
};

export function SearchPage({ client }: { client: ControlPlaneClient }) {
  const { search, navigate } = useRouter();
  const request = useMemo(() => searchRequestFromQuery(search), [search]);
  const [page, setPage] = useState<SearchPageResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const queryKey = useMemo(() => JSON.stringify({ ...request, cursor: undefined }), [request]);
  const pagination = useCursorPagination(queryKey);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPage(await client.search({ ...request, cursor: pagination.cursor }));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [client, pagination.cursor, request]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextRequest = searchRequestFromForm(new FormData(event.currentTarget));
    pagination.reset();
    navigate(`/search${searchRequestToQuery(nextRequest)}`);
  };

  const reset = () => {
    pagination.reset();
    navigate("/search");
  };

  const modeUnavailable =
    isControlPlaneError(error) && error.body.code === "unsupported_capability";

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Authorized discovery</p>
        <h1>Global search</h1>
        <p>
          Search canonical platform resources through the Control Plane. Results, counts and
          snippets are authorization-filtered before they reach this UI.
        </p>
      </header>

      <Card title="Search filters">
        <form className="form-grid" key={queryKey} onSubmit={submit} onReset={reset}>
          <label>
            Query
            <input name="q" placeholder="title, objective, ID or keyword" defaultValue={request.q ?? ""} />
          </label>
          <label>
            Exact canonical ID
            <input name="id" placeholder="task_… / project_… / run_…" defaultValue={request.id ?? ""} />
          </label>
          <label>
            Resource types
            <input name="types" placeholder="project,workspace,task,run" defaultValue={request.types?.join(",") ?? ""} />
          </label>
          <label>
            Project ID
            <input name="project_id" placeholder="project_…" defaultValue={request.project_id ?? ""} />
          </label>
          <label>
            Workspace ID
            <input name="workspace_id" placeholder="workspace_…" defaultValue={request.workspace_id ?? ""} />
          </label>
          <label>
            Status
            <input name="statuses" placeholder="running,succeeded" defaultValue={request.statuses?.join(",") ?? ""} />
          </label>
          <label>
            Tags
            <input name="tags" placeholder="search,priority" defaultValue={request.tags?.join(",") ?? ""} />
          </label>
          <label>
            Source
            <input name="sources" placeholder="canonical" defaultValue={request.sources?.join(",") ?? ""} />
          </label>
          <label>
            Provider
            <input name="providers" placeholder="control-plane" defaultValue={request.providers?.join(",") ?? ""} />
          </label>
          <label>
            Updated after
            <input name="updated_after" placeholder="2026-09-03T12:00:00+02:00" defaultValue={request.updated_after ?? ""} />
          </label>
          <label>
            Updated before
            <input name="updated_before" placeholder="2026-09-03T18:00:00+02:00" defaultValue={request.updated_before ?? ""} />
          </label>
          <label>
            Mode
            <select name="mode" defaultValue={request.mode ?? ""}>
              <option value="">Auto</option>
              <option value="exact">exact</option>
              <option value="keyword">keyword</option>
              <option value="metadata">metadata</option>
              <option value="semantic">semantic (optional)</option>
              <option value="hybrid">hybrid (optional)</option>
            </select>
          </label>
          <label>
            Sort
            <select name="sort" defaultValue={request.sort ?? DEFAULT_SEARCH.sort}>
              <option value="relevance">relevance</option>
              <option value="id">id</option>
              <option value="updated_at">updated_at</option>
            </select>
          </label>
          <label>
            Direction
            <select name="direction" defaultValue={request.direction ?? DEFAULT_SEARCH.direction}>
              <option value="desc">desc</option>
              <option value="asc">asc</option>
            </select>
          </label>
          <label>
            Limit
            <input name="limit" type="number" min={1} max={200} defaultValue={request.limit ?? DEFAULT_SEARCH.limit} />
          </label>
          <div className="actions">
            <button className="primary" type="submit">Search</button>
            <button type="reset">Reset</button>
          </div>
        </form>
      </Card>

      {modeUnavailable ? (
        <DegradedState
          title="Search mode unavailable"
          detail="The active SearchProvider does not support this optional mode. Exact, keyword and metadata search remain available."
        />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void load()} />
      ) : null}

      <Card title="Authorized results">
        {loading && !page ? <LoadingState label="Searching…" /> : null}
        {page ? <SearchResults results={page.items} /> : null}
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

function SearchResults({ results }: { results: SearchResult[] }) {
  if (!results.length) {
    return (
      <EmptyState
        title="No authorized matches"
        detail="No resource visible to the current actor matched the current search and filters."
      />
    );
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Resource</th>
            <th>Scope</th>
            <th>Status</th>
            <th>Match</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {results.map((result) => {
            const path = searchResultPath(result);
            return (
              <tr key={`${result.resource_type}:${result.resource_id}`}>
                <td>
                  <div><StatusBadge value={result.resource_type} /></div>
                  <strong>{path ? <AppLink href={path}>{result.title}</AppLink> : result.title}</strong>
                  <div><CanonicalId value={result.resource_id} /></div>
                  {result.summary ? <p>{result.summary}</p> : null}
                  {!path && result.canonical_ref ? <small>{result.canonical_ref}</small> : null}
                </td>
                <td>
                  {result.project_id ? <div>Project <CanonicalId value={result.project_id} /></div> : null}
                  {result.workspace_id ? <div>Workspace <CanonicalId value={result.workspace_id} /></div> : null}
                  {!result.project_id && !result.workspace_id ? "—" : null}
                </td>
                <td>
                  {result.status ? <StatusBadge value={result.status} /> : "—"}
                  <div><small>{result.access}{result.redacted ? " · redacted" : ""}</small></div>
                </td>
                <td>
                  <strong>{result.relevance.toFixed(2)}</strong>
                  {result.matched_fields.length ? (
                    <div><small>{result.matched_fields.join(", ")}</small></div>
                  ) : null}
                </td>
                <td>{result.updated_at ? formatDate(result.updated_at) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function searchResultPath(result: Pick<SearchResult, "resource_type" | "resource_id">): string | null {
  const id = encodeURIComponent(result.resource_id);
  switch (result.resource_type) {
    case "project":
      return `/projects/${id}`;
    case "workspace":
      return `/workspaces/${id}`;
    case "task":
      return `/tasks/${id}`;
    case "run":
      return `/runs/${id}`;
    case "artifact":
      return `/artifacts/${id}`;
    case "result":
      return `/results/${id}`;
    case "plan":
      return `/plans/${id}`;
    case "step":
      return `/steps/${id}`;
    case "model":
      return `/models/${id}`;
    case "model-provider":
      return `/models/providers/${id}`;
    case "approval":
      return `/approvals/${id}`;
    default:
      return null;
  }
}

function searchRequestFromForm(form: FormData): SearchRequest {
  const limit = Number(optional(form, "limit") ?? DEFAULT_SEARCH.limit);
  return {
    q: optional(form, "q"),
    id: optional(form, "id"),
    types: csv(form, "types"),
    project_id: optional(form, "project_id"),
    workspace_id: optional(form, "workspace_id"),
    statuses: csv(form, "statuses"),
    tags: csv(form, "tags"),
    sources: csv(form, "sources"),
    providers: csv(form, "providers"),
    updated_after: optional(form, "updated_after"),
    updated_before: optional(form, "updated_before"),
    mode: optional(form, "mode") as SearchMode | undefined,
    limit: Number.isFinite(limit) ? limit : DEFAULT_SEARCH.limit,
    sort: (optional(form, "sort") ?? DEFAULT_SEARCH.sort) as SearchSort,
    direction: (optional(form, "direction") ?? DEFAULT_SEARCH.direction) as "asc" | "desc",
  };
}

function optional(form: FormData, name: string): string | undefined {
  const value = form.get(name);
  if (typeof value !== "string") return undefined;
  const normalized = value.trim();
  return normalized || undefined;
}

function csv(form: FormData, name: string): string[] | undefined {
  const value = optional(form, name);
  if (!value) return undefined;
  const values = value.split(",").map((part) => part.trim()).filter(Boolean);
  return values.length ? Array.from(new Set(values)) : undefined;
}

export function searchRequestFromQuery(search: string): SearchRequest {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const limitValue = Number(params.get("limit") ?? DEFAULT_SEARCH.limit);
  const mode = params.get("mode");
  const sort = params.get("sort");
  const direction = params.get("direction");
  return {
    q: queryValue(params, "q"),
    id: queryValue(params, "id"),
    types: queryCsv(params, "types"),
    project_id: queryValue(params, "project_id"),
    workspace_id: queryValue(params, "workspace_id"),
    statuses: queryCsv(params, "statuses"),
    tags: queryCsv(params, "tags"),
    sources: queryCsv(params, "sources"),
    providers: queryCsv(params, "providers"),
    updated_after: queryValue(params, "updated_after"),
    updated_before: queryValue(params, "updated_before"),
    mode: isSearchMode(mode) ? mode : undefined,
    limit: Number.isInteger(limitValue) && limitValue >= 1 && limitValue <= 200
      ? limitValue
      : DEFAULT_SEARCH.limit,
    sort: isSearchSort(sort) ? sort : DEFAULT_SEARCH.sort,
    direction: direction === "asc" || direction === "desc"
      ? direction
      : DEFAULT_SEARCH.direction,
  };
}

export function searchRequestToQuery(request: SearchRequest): string {
  const params = new URLSearchParams();
  setQueryValue(params, "q", request.q);
  setQueryValue(params, "id", request.id);
  setQueryList(params, "types", request.types);
  setQueryValue(params, "project_id", request.project_id);
  setQueryValue(params, "workspace_id", request.workspace_id);
  setQueryList(params, "statuses", request.statuses);
  setQueryList(params, "tags", request.tags);
  setQueryList(params, "sources", request.sources);
  setQueryList(params, "providers", request.providers);
  setQueryValue(params, "updated_after", request.updated_after);
  setQueryValue(params, "updated_before", request.updated_before);
  setQueryValue(params, "mode", request.mode);
  if (request.limit !== undefined && request.limit !== DEFAULT_SEARCH.limit) {
    params.set("limit", String(request.limit));
  }
  if (request.sort && request.sort !== DEFAULT_SEARCH.sort) params.set("sort", request.sort);
  if (request.direction && request.direction !== DEFAULT_SEARCH.direction) {
    params.set("direction", request.direction);
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

function queryValue(params: URLSearchParams, name: string): string | undefined {
  const value = params.get(name)?.trim();
  return value || undefined;
}

function queryCsv(params: URLSearchParams, name: string): string[] | undefined {
  const value = queryValue(params, name);
  if (!value) return undefined;
  const values = value.split(",").map((part) => part.trim()).filter(Boolean);
  return values.length ? Array.from(new Set(values)) : undefined;
}

function setQueryValue(
  params: URLSearchParams,
  name: string,
  value: string | undefined,
): void {
  if (value) params.set(name, value);
}

function setQueryList(
  params: URLSearchParams,
  name: string,
  values: string[] | undefined,
): void {
  if (values?.length) params.set(name, values.join(","));
}

function isSearchMode(value: string | null): value is SearchMode {
  return value !== null && ["exact", "keyword", "metadata", "semantic", "hybrid"].includes(value);
}

function isSearchSort(value: string | null): value is SearchSort {
  return value !== null && ["relevance", "id", "updated_at"].includes(value);
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
