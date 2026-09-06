import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  RegistryClient,
  type RegistryDependency,
  type RegistryItem,
  type RegistryItemType,
  type RegistryPreview,
  type RegistryTrustStatus,
} from "../api/registry";
import type { ListQuery, Page } from "../api/types";
import {
  CanonicalId,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const ITEM_TYPES: Array<{ value: RegistryItemType; label: string }> = [
  { value: "agent", label: "Agents" },
  { value: "agent_team", label: "Agent Teams" },
  { value: "tool", label: "Tools" },
  { value: "plugin", label: "Plugins" },
  { value: "workflow", label: "Workflows" },
  { value: "template", label: "Templates" },
  { value: "model_configuration", label: "Model configurations" },
  { value: "connector", label: "Connectors" },
  { value: "evaluation", label: "Evaluations" },
  { value: "documentation", label: "Documentation" },
];

const TRUST_STATES: RegistryTrustStatus[] = ["trusted", "reviewed", "local", "untrusted"];
const PAGE_SIZE = 50;

export function MarketplacePage({ client }: { client: RegistryClient }) {
  const [page, setPage] = useState<Page<RegistryItem> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [queryText, setQueryText] = useState("");
  const [itemType, setItemType] = useState<RegistryItemType | "">("");
  const [trustStatus, setTrustStatus] = useState<RegistryTrustStatus | "">("");
  const [tag, setTag] = useState("");
  const [category, setCategory] = useState("");
  const [license, setLicense] = useState("");
  const [publisher, setPublisher] = useState("");
  const [requiredCapability, setRequiredCapability] = useState("");
  const [platformVersion, setPlatformVersion] = useState("");
  const [updatesOnly, setUpdatesOnly] = useState(false);
  const [selected, setSelected] = useState<RegistryItem | null>(null);
  const [preview, setPreview] = useState<RegistryPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const listQuery = useMemo<ListQuery>(() => {
    const filters: Record<string, string> = {};
    if (itemType) filters.item_type = itemType;
    if (trustStatus) filters.trust_status = trustStatus;
    if (tag.trim()) filters.tag = tag.trim();
    if (category.trim()) filters.category = category.trim();
    if (license.trim()) filters.license = license.trim();
    if (publisher.trim()) filters.publisher = publisher.trim();
    if (requiredCapability.trim()) filters.required_capability = requiredCapability.trim();
    if (platformVersion.trim()) filters.platform_version = platformVersion.trim();
    if (updatesOnly) filters.update_available = "true";
    return {
      limit: PAGE_SIZE,
      sort: "name",
      direction: "asc",
      q: queryText.trim() || undefined,
      filters,
    };
  }, [
    category,
    itemType,
    license,
    platformVersion,
    publisher,
    queryText,
    requiredCapability,
    tag,
    trustStatus,
    updatesOnly,
  ]);

  const load = useCallback(
    async (cursor?: string, append = false) => {
      if (append) setLoadingMore(true);
      try {
        const next = await client.list({ ...listQuery, cursor });
        setPage((current) => {
          if (!append || !current) return next;
          return {
            ...next,
            items: [...current.items, ...next.items],
          };
        });
        setError(null);
        setSelected((current) => {
          if (!current) return current;
          return next.items.find((item) => item.id === current.id) ?? current;
        });
      } catch (nextError) {
        setError(nextError);
      } finally {
        if (append) setLoadingMore(false);
      }
    },
    [client, listQuery],
  );

  useEffect(() => void load(), [load]);

  const selectItem = (item: RegistryItem) => {
    setSelected(item);
    setPreview(null);
    setActionError(null);
  };

  const runPreview = async (item: RegistryItem) => {
    setBusy(true);
    setActionError(null);
    try {
      const next = await client.preview(item.item_id, item.version);
      setPreview(next);
      setSelected(next.item);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const activate = async () => {
    if (!selected || !preview || preview.id !== selected.id || !preview.activation_allowed) return;
    setBusy(true);
    setActionError(null);
    try {
      await client.activate(selected.item_id, selected.version);
      await load();
      setPreview(await client.preview(selected.item_id, selected.version));
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const pin = async () => {
    if (!selected || !selected.installed_version) return;
    setBusy(true);
    setActionError(null);
    try {
      await client.pin(selected.item_id, selected.installed_version);
      await refreshSelected(selected);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const unpin = async () => {
    if (!selected) return;
    setBusy(true);
    setActionError(null);
    try {
      await client.unpin(selected.item_id);
      await refreshSelected(selected);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const refreshSelected = async (item: RegistryItem) => {
    const refreshed = await client.get(item.item_id, item.version);
    setSelected(refreshed);
    setPreview(null);
    await load();
  };

  const resetFilters = (event: FormEvent) => {
    event.preventDefault();
    setQueryText("");
    setItemType("");
    setTrustStatus("");
    setTag("");
    setCategory("");
    setLicense("");
    setPublisher("");
    setRequiredCapability("");
    setPlatformVersion("");
    setUpdatesOnly(false);
  };

  const selectedIsInstalledVersion =
    selected !== null && selected.installed_version === selected.version;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Registry & distribution</p>
        <h1>Marketplace</h1>
        <p>
          Discover reusable platform assets, inspect provenance and requested privileges, preview
          compatibility and install only through the canonical owner domain.
        </p>
      </header>

      <Card title="Discover">
        <form className="toolbar" onSubmit={resetFilters}>
          <label>
            Search
            <input
              type="search"
              value={queryText}
              placeholder="Agent, workflow, publisher, category, tag…"
              onChange={(event) => setQueryText(event.target.value)}
            />
          </label>
          <label>
            Type
            <select
              value={itemType}
              onChange={(event) => setItemType(event.target.value as RegistryItemType | "")}
            >
              <option value="">All types</option>
              {ITEM_TYPES.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </label>
          <label>
            Trust
            <select
              value={trustStatus}
              onChange={(event) => setTrustStatus(event.target.value as RegistryTrustStatus | "")}
            >
              <option value="">All trust states</option>
              {TRUST_STATES.map((status) => (
                <option key={status} value={status}>{status}</option>
              ))}
            </select>
          </label>
          <label>
            Tags
            <input
              value={tag}
              placeholder="music, analysis"
              onChange={(event) => setTag(event.target.value)}
            />
          </label>
          <label>
            Categories
            <input
              value={category}
              placeholder="research"
              onChange={(event) => setCategory(event.target.value)}
            />
          </label>
          <label>
            License
            <input value={license} placeholder="MIT" onChange={(event) => setLicense(event.target.value)} />
          </label>
          <label>
            Publisher
            <input
              value={publisher}
              placeholder="publisher ID"
              onChange={(event) => setPublisher(event.target.value)}
            />
          </label>
          <label>
            Required capability
            <input
              value={requiredCapability}
              placeholder="capability.id"
              onChange={(event) => setRequiredCapability(event.target.value)}
            />
          </label>
          <label>
            Platform version
            <input
              value={platformVersion}
              placeholder="0.1.0"
              onChange={(event) => setPlatformVersion(event.target.value)}
            />
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={updatesOnly}
              onChange={(event) => setUpdatesOnly(event.target.checked)}
            />
            Updates only
          </label>
          <button type="submit">Reset filters</button>
        </form>

        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
        {!page && !error ? <LoadingState label="Loading Marketplace…" /> : null}
        {page && page.items.length === 0 ? (
          <EmptyState
            title="No Registry items match"
            detail="Change the search or filters, or configure a catalog containing compatible assets."
          />
        ) : null}
        {page && page.items.length > 0 ? (
          <div className="stack">
            <div className="card-grid">
              {page.items.map((item) => (
                <article className="card" key={item.id}>
                  <div className="button-row">
                    <StatusBadge value={item.item_type} />
                    <StatusBadge value={item.trust_status} />
                    {item.installed_version === item.version ? <StatusBadge value="installed" /> : null}
                    {item.update_available ? <StatusBadge value="update_available" /> : null}
                    {item.pinned_version ? <StatusBadge value="pinned" /> : null}
                  </div>
                  <h2>{item.name}</h2>
                  <p>{item.description}</p>
                  <dl className="detail-grid">
                    <dt>Publisher</dt><dd>{item.publisher}</dd>
                    <dt>Version</dt><dd>{item.version}</dd>
                    <dt>License</dt><dd>{item.license}</dd>
                    <dt>ID</dt><dd><CanonicalId value={item.item_id} /></dd>
                  </dl>
                  <button type="button" onClick={() => selectItem(item)}>Inspect</button>
                </article>
              ))}
            </div>
            {page.next_cursor ? (
              <button
                type="button"
                disabled={loadingMore}
                onClick={() => void load(page.next_cursor ?? undefined, true)}
              >
                {loadingMore ? "Loading more…" : "Load more"}
              </button>
            ) : null}
          </div>
        ) : null}
      </Card>

      {selected ? (
        <Card title={`${selected.name} ${selected.version}`}>
          <div className="button-row">
            <StatusBadge value={selected.trust_status} />
            <StatusBadge value={selected.route} />
            {selected.deprecated ? <StatusBadge value="deprecated" /> : null}
            {selected.yanked ? <StatusBadge value="yanked" /> : null}
            {selected.update_available ? <StatusBadge value="update_available" /> : null}
          </div>

          <dl className="detail-grid">
            <dt>Canonical ID</dt><dd><CanonicalId value={selected.item_id} /></dd>
            <dt>Publisher</dt><dd>{selected.publisher}</dd>
            <dt>Source</dt><dd>{selected.source.repository}</dd>
            <dt>Package</dt><dd>{selected.source.package_reference ?? "—"}</dd>
            <dt>Revision</dt><dd>{selected.source.revision ?? "—"}</dd>
            <dt>License</dt><dd>{selected.license}</dd>
            <dt>Provenance</dt><dd>{selected.provenance}</dd>
            <dt>Signature</dt>
            <dd>
              {selected.integrity.signature_present
                ? `present (${selected.integrity.signature_key_id ?? "no key ID"})`
                : "not declared"}
            </dd>
            <dt>Installed version</dt><dd>{selected.installed_version ?? "not installed"}</dd>
            <dt>Pinned version</dt><dd>{selected.pinned_version ?? "not pinned"}</dd>
            <dt>Supported platform</dt>
            <dd>
              {selected.minimum_platform_version ?? "any"} – {selected.maximum_platform_version ?? "any"}
            </dd>
          </dl>

          {selected.update_available && selected.pinned_version ? (
            <p>
              A newer version is available, but the installed version is pinned. Unpin before
              applying an update.
            </p>
          ) : null}

          <DependencyList dependencies={selected.dependencies} />
          <RequirementList title="Requested permissions" values={selected.requested_permissions} />
          <RequirementList title="Required capabilities" values={selected.required_capabilities} />
          <RequirementList title="Required plugins" values={selected.required_plugins} />
          <RequirementList title="Required connectors" values={selected.required_connectors} />
          <RequirementList title="Required models" values={selected.required_models} />

          {selected.changelog ? (
            <div>
              <h3>Changelog</h3>
              <p>{selected.changelog}</p>
            </div>
          ) : null}

          <div className="button-row">
            <button type="button" disabled={busy} onClick={() => void runPreview(selected)}>
              Validate & preview
            </button>
            {selected.installed && !selected.pinned_version ? (
              <button type="button" disabled={busy} onClick={() => void pin()}>Pin installed version</button>
            ) : null}
            {selected.pinned_version ? (
              <button type="button" disabled={busy} onClick={() => void unpin()}>Unpin</button>
            ) : null}
          </div>

          {actionError ? <ErrorState error={actionError} /> : null}

          {preview ? (
            <div className="stack">
              <h3>Activation preview</h3>
              <p>
                Provider <strong>{preview.provider_id}</strong>. Activation is
                {preview.activation_allowed ? " allowed" : " blocked"} after server-side checks.
              </p>
              {preview.findings.length === 0 ? (
                <div className="state"><strong>No validation findings</strong></div>
              ) : (
                <ul>
                  {preview.findings.map((finding) => (
                    <li key={`${finding.severity}:${finding.code}`}>
                      <StatusBadge value={finding.severity} /> {finding.code}: {finding.message}
                    </li>
                  ))}
                </ul>
              )}
              {preview.activation_allowed && selected.route !== "manual" && !selectedIsInstalledVersion ? (
                <button type="button" disabled={busy} onClick={() => void activate()}>
                  {selected.update_available ? "Apply explicit update" : "Install / activate"}
                </button>
              ) : null}
              {selectedIsInstalledVersion ? <p>This exact version is already installed.</p> : null}
              {selected.route === "manual" ? (
                <p>This asset is distributed for manual use and cannot be activated automatically.</p>
              ) : null}
            </div>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

function RequirementList({ title, values }: { title: string; values: string[] }) {
  if (values.length === 0) return null;
  return (
    <div>
      <h3>{title}</h3>
      <ul>{values.map((value) => <li key={value}>{value}</li>)}</ul>
    </div>
  );
}

function DependencyList({ dependencies }: { dependencies: RegistryDependency[] }) {
  if (dependencies.length === 0) return null;
  return (
    <div>
      <h3>Registry dependencies</h3>
      <ul>
        {dependencies.map((dependency) => (
          <li key={dependency.item_id}>
            <CanonicalId value={dependency.item_id} /> — {dependency.optional ? "optional" : "required"};
            versions {dependency.minimum_version ?? "any"} – {dependency.maximum_version ?? "any"}
          </li>
        ))}
      </ul>
    </div>
  );
}
