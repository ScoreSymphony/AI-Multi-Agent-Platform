import { useCallback, useEffect, useState } from "react";
import {
  RepositoryCollectionClient,
  type CanonicalRepository,
  type RepositoryCommitView,
  type RepositoryDiffView,
  type RepositoryStatusView,
} from "../api/repositories";
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

export function RepositoriesPage({ client }: { client: RepositoryCollectionClient }) {
  const [page, setPage] = useState<Page<CanonicalRepository> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    try {
      setPage(await client.list({ limit: 100, q: query.trim() || undefined }));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, query]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical source control</p>
        <h1>Repositories</h1>
        <p>
          Provider-neutral repository references registered with the Control Plane. Provider-native
          identifiers remain external metadata rather than platform identity.
        </p>
      </header>

      <Card title="Repository inventory">
        <div className="actions">
          <label>
            Search
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Repository name, host, branch or canonical ID"
            />
          </label>
          <button onClick={() => void load()}>Refresh</button>
        </div>
      </Card>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {!page && !error ? <LoadingState /> : null}
      {page ? (
        page.items.length ? (
          <div className="grid-two">
            {page.items.map((repository) => (
              <Card key={repository.id} title={repositoryLabel(repository)}>
                <div className="stack compact-stack">
                  <CanonicalId value={repository.id} />
                  <p>
                    <StatusBadge value={repository.visibility} /> · default branch {repository.default_branch ?? "unknown"}
                  </p>
                  <p>Connection: <code>{repository.connection_id}</code></p>
                  <p>Resolved revision: <Revision value={repository.resolved_revision} /></p>
                  <p>{supportedCount(repository)} supported repository capabilities.</p>
                  <AppLink href={`/repositories/${encodeURIComponent(repository.id)}`}>
                    Inspect repository
                  </AppLink>
                </div>
              </Card>
            ))}
          </div>
        ) : (
          <EmptyState title="No authorized repositories found" />
        )
      ) : null}
    </div>
  );
}

export function RepositoryDetailPage({
  client,
  repositoryId,
}: {
  client: RepositoryCollectionClient;
  repositoryId: string;
}) {
  const [repository, setRepository] = useState<CanonicalRepository | null>(null);
  const [status, setStatus] = useState<RepositoryStatusView | null>(null);
  const [branches, setBranches] = useState<string[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [commits, setCommits] = useState<RepositoryCommitView[]>([]);
  const [diff, setDiff] = useState<RepositoryDiffView | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const nextRepository = await client.get(repositoryId);
      setRepository(nextRepository);
      const operations = supportedOperations(nextRepository);
      const [nextStatus, nextRefs, nextDiff] = await Promise.all([
        operations.has("repository.status") ? client.status(repositoryId) : Promise.resolve(null),
        operations.has("repository.inspect_refs")
          ? Promise.all([
              client.branches(repositoryId),
              client.tags(repositoryId),
              client.commits(repositoryId),
            ])
          : Promise.resolve<[string[], string[], RepositoryCommitView[]]>([[], [], []]),
        operations.has("repository.diff") ? client.diff(repositoryId) : Promise.resolve(null),
      ]);
      setStatus(nextStatus);
      setBranches(nextRefs[0]);
      setTags(nextRefs[1]);
      setCommits(nextRefs[2]);
      setDiff(nextDiff);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, repositoryId]);

  useEffect(() => {
    void load();
  }, [load]);

  const fetchRepository = async () => {
    setBusy(true);
    try {
      await client.fetch(repositoryId);
      await load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error && !repository) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!repository) return <LoadingState />;
  const operations = supportedOperations(repository);

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical repository</p>
        <h1>{repositoryLabel(repository)}</h1>
        <CanonicalId value={repository.id} />
        <p>
          Connection <code>{repository.connection_id}</code> · <StatusBadge value={repository.visibility} />
        </p>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      <div className="metrics">
        <Metric label="Default branch" value={repository.default_branch ?? "Unknown"} />
        <Metric label="Target ref" value={repository.target_revision ?? "—"} />
        <Metric label="Resolved revision" value={shortRevision(repository.resolved_revision)} />
        <Metric label="Working tree" value={status ? (status.clean ? "Clean" : "Changed") : "Unavailable"} />
      </div>

      <Card title="Repository operations">
        <div className="actions">
          <button onClick={() => void load()} disabled={busy}>Refresh</button>
          {operations.has("repository.fetch") ? (
            <button onClick={() => void fetchRepository()} disabled={busy}>
              Fetch revisions
            </button>
          ) : null}
        </div>
        <p>
          Actions are routed through canonical Control Plane commands; repository policy and
          approval checks remain authoritative on the server.
        </p>
      </Card>

      <div className="grid-two">
        <Card title="Capabilities">
          {repository.capabilities.length ? (
            <ul>
              {repository.capabilities.map((capability) => (
                <li key={capability.operation}>
                  <code>{capability.operation}</code> — {capability.supported ? "supported" : "unavailable"}
                  {capability.side_effects !== "none" ? ` · ${capability.side_effects}` : ""}
                  {capability.requires_credentials ? " · credentials required" : ""}
                </li>
              ))}
            </ul>
          ) : <p>No capability metadata is available.</p>}
        </Card>

        <Card title="Working tree">
          {status ? (
            <div className="stack compact-stack">
              <p>Branch: <code>{status.branch ?? "detached"}</code></p>
              <p>HEAD: <Revision value={status.head_revision} /></p>
              <PathSummary label="Staged" paths={status.staged_paths} />
              <PathSummary label="Modified" paths={status.modified_paths} />
              <PathSummary label="Deleted" paths={status.deleted_paths} />
              <PathSummary label="Untracked" paths={status.untracked_paths} />
            </div>
          ) : <p>Status inspection is not supported by this repository provider.</p>}
        </Card>

        <Card title="Branches & tags">
          <p><strong>Branches:</strong> {branches.length ? branches.join(", ") : "none or unavailable"}</p>
          <p><strong>Tags:</strong> {tags.length ? tags.join(", ") : "none or unavailable"}</p>
        </Card>

        <Card title="Recent commits">
          {commits.length ? (
            <ol>
              {commits.slice(0, 10).map((commit) => (
                <li key={commit.revision}>
                  <code>{shortRevision(commit.revision)}</code> {commit.message}
                </li>
              ))}
            </ol>
          ) : <p>No commit history is available through this provider.</p>}
        </Card>
      </div>

      <Card title="Current diff">
        {diff ? (
          diff.changed_paths.length || diff.patch ? (
            <div className="stack compact-stack">
              <p>Changed paths: {diff.changed_paths.length ? diff.changed_paths.join(", ") : "none"}</p>
              <pre>{diff.patch || "No patch content."}</pre>
            </div>
          ) : <p>No working-tree diff.</p>
        ) : <p>Diff inspection is not supported by this repository provider.</p>}
      </Card>
    </div>
  );
}

function supportedOperations(repository: CanonicalRepository): Set<string> {
  return new Set(
    repository.capabilities
      .filter((capability) => capability.supported)
      .map((capability) => capability.operation),
  );
}

function supportedCount(repository: CanonicalRepository): number {
  return repository.capabilities.filter((capability) => capability.supported).length;
}

function repositoryLabel(repository: CanonicalRepository): string {
  const name = repository.metadata.name;
  return typeof name === "string" && name.trim() ? name : repository.id;
}

function Revision({ value }: { value: string | null }) {
  return <code title={value ?? undefined}>{shortRevision(value)}</code>;
}

function shortRevision(value: string | null): string {
  if (!value) return "—";
  return value.length > 12 ? value.slice(0, 12) : value;
}

function PathSummary({ label, paths }: { label: string; paths: string[] }) {
  return <p><strong>{label}:</strong> {paths.length ? paths.join(", ") : "none"}</p>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
