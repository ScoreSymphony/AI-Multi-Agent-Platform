import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  RepositoryCollectionClient,
  type CanonicalRepository,
  type RepositoryCommitView,
  type RepositoryDiffView,
  type RepositoryDiscoveryView,
  type RepositoryStatusView,
} from "../api/repositories";
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

export function RepositoriesPage({ client }: { client: RepositoryCollectionClient }) {
  const [page, setPage] = useState<Page<CanonicalRepository> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<RepositoryDiscoveryView | null>(null);

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

  const attachLocal = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const projectId = String(form.get("project_id") ?? "").trim();
    const name = String(form.get("name") ?? "").trim();
    const initialize = form.get("initialize") === "on";
    const defaultBranch = String(form.get("default_branch") ?? "main").trim();
    const approvalId = blankToUndefined(String(form.get("approval_id") ?? ""));

    if (!window.confirm(`Attach managed local repository ${name} to project ${projectId}?`)) return;
    setBusy("attach-local");
    setActionError(null);
    try {
      await client.attachLocal(projectId, { name, initialize, defaultBranch, approvalId });
      formElement.reset();
      await load();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(null);
    }
  };

  const discoverRepositories = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const connectionId = String(form.get("connection_id") ?? "").trim();
    const providerId = String(form.get("provider_id") ?? "").trim();
    const attach = form.get("attach") === "on";
    const approvalId = blankToUndefined(String(form.get("approval_id") ?? ""));

    if (
      attach
      && !window.confirm(
        `Discover and attach repositories from provider ${providerId} on Connection ${connectionId}?`,
      )
    ) {
      return;
    }
    setBusy("discover");
    setActionError(null);
    try {
      const result = await client.discover(connectionId, providerId, { attach, approvalId });
      setDiscovery(result);
      if (attach) await load();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(null);
    }
  };

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

      <div className="grid-two">
        <Card title="Attach managed local repository">
          <form className="form-grid" onSubmit={attachLocal}>
            <label>Project ID<input required name="project_id" placeholder="project_…" /></label>
            <label>Managed name<input required name="name" placeholder="scoresymphony" /></label>
            <label>Default branch<input required name="default_branch" defaultValue="main" /></label>
            <label><input type="checkbox" name="initialize" /> Initialize if absent</label>
            <label>Approval ID<input name="approval_id" placeholder="optional approval_…" /></label>
            <button className="primary" type="submit" disabled={busy !== null}>
              {busy === "attach-local" ? "Attaching…" : "Attach local repository"}
            </button>
          </form>
          <p className="muted">
            Only a managed repository name is accepted. The browser never submits an arbitrary host
            filesystem path.
          </p>
        </Card>

        <Card title="Discover provider repositories">
          <form className="form-grid" onSubmit={discoverRepositories}>
            <label>Connection ID<input required name="connection_id" placeholder="connection_…" /></label>
            <label>Provider ID<input required name="provider_id" placeholder="github" /></label>
            <label><input type="checkbox" name="attach" /> Attach discovered repositories</label>
            <label>Approval ID<input name="approval_id" placeholder="optional approval_…" /></label>
            <button className="primary" type="submit" disabled={busy !== null}>
              {busy === "discover" ? "Discovering…" : "Discover repositories"}
            </button>
          </form>
          <p className="muted">
            Discovery uses the canonical Connection/provider binding; provider credentials and SDKs
            remain behind the Control Plane.
          </p>
        </Card>
      </div>

      {actionError ? <ErrorState error={actionError} onRetry={() => setActionError(null)} /> : null}
      {discovery ? (
        <Card title={discovery.attached ? "Attached discovery result" : "Discovery result"}>
          {discovery.repositories.length ? (
            <ul className="reference-list">
              {discovery.repositories.map((repository) => (
                <li key={repository.id}>
                  {discovery.attached ? (
                    <AppLink href={`/repositories/${encodeURIComponent(repository.id)}`}>
                      <CanonicalId value={repository.id} />
                    </AppLink>
                  ) : (
                    <CanonicalId value={repository.id} />
                  )}
                  {" · "}{repositoryLabel(repository)}
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No repositories discovered" />}
        </Card>
      ) : null}

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
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [detached, setDetached] = useState(false);
  const [approvalId, setApprovalId] = useState("");

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

  const mutate = async (
    key: string,
    operation: () => Promise<unknown>,
    options: { reload?: boolean; success?: () => void } = {},
  ) => {
    setBusy(key);
    setActionError(null);
    try {
      await operation();
      options.success?.();
      if (options.reload !== false) await load();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(null);
    }
  };

  const createBranch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await mutate("branch", () => client.createBranch(
      repositoryId,
      String(form.get("name") ?? ""),
      {
        startRevision: String(form.get("start_revision") ?? "HEAD"),
        checkout: form.get("checkout") === "on",
        approvalId: blankToUndefined(approvalId),
      },
    ));
  };

  const checkout = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const revision = String(form.get("revision") ?? "").trim();
    if (!window.confirm(`Checkout ${revision} in repository ${repositoryId}?`)) return;
    await mutate("checkout", () => client.checkout(
      repositoryId,
      revision,
      blankToUndefined(approvalId),
    ));
  };

  const commit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await mutate("commit", () => client.commit(
      repositoryId,
      {
        message: String(form.get("message") ?? ""),
        authorName: String(form.get("author_name") ?? ""),
        authorEmail: String(form.get("author_email") ?? ""),
        approvalId: blankToUndefined(approvalId),
      },
    ));
  };

  const push = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const remote = String(form.get("remote") ?? "origin").trim();
    const refspec = blankToUndefined(String(form.get("refspec") ?? ""));
    if (!window.confirm(`Push repository ${repositoryId} to remote ${remote}?`)) return;
    await mutate("push", () => client.push(
      repositoryId,
      { remote, refspec, approvalId: blankToUndefined(approvalId) },
    ));
  };

  const detach = async () => {
    if (
      !window.confirm(
        `Detach repository ${repositoryId} from the platform? Provider-owned repository content will not be deleted.`,
      )
    ) {
      return;
    }
    await mutate(
      "detach",
      () => client.detach(repositoryId, blankToUndefined(approvalId)),
      { reload: false, success: () => setDetached(true) },
    );
  };

  if (detached) {
    return (
      <div className="stack">
        <header className="page-header"><p className="eyebrow">Canonical repository</p><h1>Repository detached</h1></header>
        <Card title="Provider content preserved">
          <p>
            The platform-owned repository binding was removed. The provider-owned repository was not
            deleted or mutated by detach.
          </p>
          <AppLink href="/repositories">Back to Repositories</AppLink>
        </Card>
      </div>
    );
  }

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
      {actionError ? <ErrorState error={actionError} onRetry={() => setActionError(null)} /> : null}

      <div className="metrics">
        <Metric label="Default branch" value={repository.default_branch ?? "Unknown"} />
        <Metric label="Target ref" value={repository.target_revision ?? "—"} />
        <Metric label="Resolved revision" value={shortRevision(repository.resolved_revision)} />
        <Metric label="Working tree" value={status ? (status.clean ? "Clean" : "Changed") : "Unavailable"} />
      </div>

      <Card title="Repository operations">
        <label>
          Approval ID
          <input
            value={approvalId}
            onChange={(event) => setApprovalId(event.currentTarget.value)}
            placeholder="optional approval_…"
          />
        </label>
        <div className="actions">
          <button onClick={() => void load()} disabled={busy !== null}>Refresh</button>
          {operations.has("repository.fetch") ? (
            <button
              onClick={() => void mutate(
                "fetch",
                () => client.fetch(repositoryId, blankToUndefined(approvalId)),
              )}
              disabled={busy !== null}
            >
              {busy === "fetch" ? "Fetching…" : "Fetch revisions"}
            </button>
          ) : null}
          <button className="danger" onClick={() => void detach()} disabled={busy !== null}>
            {busy === "detach" ? "Detaching…" : "Detach repository"}
          </button>
        </div>
        <p>
          Actions are routed through canonical Control Plane commands; repository policy and
          approval checks remain authoritative on the server.
        </p>
      </Card>

      <div className="grid-two">
        {operations.has("repository.create_branch") ? (
          <Card title="Create branch">
            <form className="form-grid" onSubmit={createBranch}>
              <label>Branch name<input required name="name" placeholder="feature/web" /></label>
              <label>Start revision<input required name="start_revision" defaultValue="HEAD" /></label>
              <label><input type="checkbox" name="checkout" /> Checkout after creation</label>
              <button type="submit" disabled={busy !== null}>
                {busy === "branch" ? "Creating…" : "Create branch"}
              </button>
            </form>
          </Card>
        ) : null}

        {operations.has("repository.checkout") ? (
          <Card title="Checkout revision">
            <form className="form-grid" onSubmit={checkout}>
              <label>Revision or ref<input required name="revision" placeholder="main" /></label>
              <button type="submit" disabled={busy !== null}>
                {busy === "checkout" ? "Checking out…" : "Checkout"}
              </button>
            </form>
          </Card>
        ) : null}

        {operations.has("repository.commit") ? (
          <Card title="Commit current changes">
            <form className="form-grid" onSubmit={commit}>
              <label>Message<input required name="message" /></label>
              <label>Author name<input required name="author_name" /></label>
              <label>Author email<input required type="email" name="author_email" /></label>
              <button type="submit" disabled={busy !== null}>
                {busy === "commit" ? "Committing…" : "Create commit"}
              </button>
            </form>
          </Card>
        ) : null}

        {operations.has("repository.push") ? (
          <Card title="Push">
            <form className="form-grid" onSubmit={push}>
              <label>Remote<input required name="remote" defaultValue="origin" /></label>
              <label>Refspec<input name="refspec" placeholder="optional" /></label>
              <button className="primary" type="submit" disabled={busy !== null}>
                {busy === "push" ? "Pushing…" : "Push"}
              </button>
            </form>
          </Card>
        ) : null}
      </div>

      {!["repository.create_branch", "repository.checkout", "repository.commit", "repository.push"]
        .some((operation) => operations.has(operation)) ? (
        <DegradedState
          title="Repository mutation capabilities unavailable"
          detail="This provider exposes read/inspection state only. The Web UI does not invent unsupported Git mutations or call a provider-private API."
        />
      ) : null}

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
              {commits.slice(0, 10).map((item) => (
                <li key={item.revision}>
                  <code>{shortRevision(item.revision)}</code> {item.message}
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

      <div className="actions"><AppLink href="/repositories">Back to Repositories</AppLink></div>
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

function blankToUndefined(value: string): string | undefined {
  const normalized = value.trim();
  return normalized || undefined;
}

function PathSummary({ label, paths }: { label: string; paths: string[] }) {
  return <p><strong>{label}:</strong> {paths.length ? paths.join(", ") : "none"}</p>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
