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
import {
  TECHNICAL_CATEGORIES,
  technicalCategoryLabel,
  technicalMetadata,
} from "../marketplace/technical";

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
  const [technicalOnly, setTechnicalOnly] = useState(true);
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
    if (technicalOnly) filters.technical_component = "true";
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
    technicalOnly,
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
    setTechnicalOnly(true);
    setUpdatesOnly(false);
  };

  const selectedIsInstalledVersion =
    selected !== null && selected.installed_version === selected.version;
  const selectedTechnical = selected ? technicalMetadata(selected) : null;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Technical component ecosystem</p>
        <h1>Marketplace</h1>
        <p>
          Discover and compare reusable developer and agent infrastructure. A catalog listing is
          discovery evidence, not an adoption or trust decision; source, license and provenance stay
          visible before any activation path.
        </p>
      </header>

      <Card title="Technical categories">
        <p>
          The technical taxonomy is product-facing metadata layered on the canonical Registry item
          types. Use the generic Registry view when you intentionally want connectors, templates and
          other non-technical assets as well.
        </p>
        <div className="button-row">
          <button
            type="button"
            onClick={() => {
              setTechnicalOnly(true);
              setCategory("");
            }}
          >
            All technical components
          </button>
          {TECHNICAL_CATEGORIES.map(([id, label]) => (
            <button
              type="button"
              key={id}
              onClick={() => {
                setTechnicalOnly(true);
                setCategory(id);
              }}
            >
              {label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => {
              setTechnicalOnly(false);
              setCategory("");
            }}
          >
            All Registry assets
          </button>
        </div>
      </Card>

      <Card title="Discover">
        <form className="toolbar" onSubmit={resetFilters}>
          <label>
            Search
            <input
              type="search"
              value={queryText}
              placeholder="Framework, coding agent, runtime, publisher…"
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
              placeholder="lifecycle:candidate"
              onChange={(event) => setTag(event.target.value)}
            />
          </label>
          <label>
            Category
            <input
              value={category}
              placeholder="code-intelligence"
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
              checked={technicalOnly}
              onChange={(event) => setTechnicalOnly(event.target.checked)}
            />
            Technical components only
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
            title="No Marketplace items match"
            detail="Change the technical category or filters, or configure a catalog containing matching assets."
          />
        ) : null}
        {page && page.items.length > 0 ? (
          <div className="stack">
            <div className="card-grid">
              {page.items.map((item) => {
                const technical = technicalMetadata(item);
                return (
                  <article className="card" key={item.id}>
                    <div className="button-row">
                      <StatusBadge value={item.item_type} />
                      <StatusBadge value={item.trust_status} />
                      {technical ? <StatusBadge value={technical.lifecycle} /> : null}
                      {technical && technical.evaluation !== "unknown" ? (
                        <StatusBadge value={`evaluation:${technical.evaluation}`} />
                      ) : null}
                      {item.installed_version === item.version ? <StatusBadge value="installed" /> : null}
                      {item.update_available ? <StatusBadge value="update_available" /> : null}
                      {item.pinned_version ? <StatusBadge value="pinned" /> : null}
                    </div>
                    <h2>{item.name}</h2>
                    <p>{item.description}</p>
                    {technical ? (
                      <p>
                        {technical.categories.map(technicalCategoryLabel).join(" · ")}
                      </p>
                    ) : null}
                    <dl className="detail-grid">
                      <dt>Publisher</dt><dd>{item.publisher}</dd>
                      <dt>Version</dt><dd>{item.version}</dd>
                      <dt>License</dt><dd>{item.license}</dd>
                      {technical ? <><dt>Deployment</dt><dd>{technical.deploymentModes.join(", ")}</dd></> : null}
                      {technical ? <><dt>Cost</dt><dd>{technical.costStatus}</dd></> : null}
                      <dt>ID</dt><dd><CanonicalId value={item.item_id} /></dd>
                    </dl>
                    <button type="button" onClick={() => selectItem(item)}>Inspect</button>
                  </article>
                );
              })}
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
            {selectedTechnical ? <StatusBadge value={selectedTechnical.lifecycle} /> : null}
            {selectedTechnical ? <StatusBadge value={`evaluation:${selectedTechnical.evaluation}`} /> : null}
            {selected.deprecated ? <StatusBadge value="deprecated" /> : null}
            {selected.yanked ? <StatusBadge value="yanked" /> : null}
            {selected.update_available ? <StatusBadge value="update_available" /> : null}
          </div>

          {selectedTechnical ? (
            <dl className="detail-grid">
              <dt>Technical categories</dt>
              <dd>{selectedTechnical.categories.map(technicalCategoryLabel).join(", ")}</dd>
              <dt>Lifecycle</dt><dd>{selectedTechnical.lifecycle}</dd>
              <dt>Evaluation</dt><dd>{selectedTechnical.evaluation}</dd>
              <dt>Deployment</dt><dd>{selectedTechnical.deploymentModes.join(", ")}</dd>
              <dt>Cost policy</dt><dd>{selectedTechnical.costStatus}</dd>
              <dt>Network</dt><dd>{selectedTechnical.networkStatus}</dd>
              <dt>Resource class</dt><dd>{selectedTechnical.resourceClass ?? "unknown"}</dd>
              <dt>Provider requirements</dt>
              <dd>{selectedTechnical.providerRequirements.join(", ") || "unknown / none recorded"}</dd>
              <dt>Alternatives</dt>
              <dd>{selectedTechnical.alternatives.join(", ") || "not recorded"}</dd>
              <dt>Architecture reference</dt><dd>{selectedTechnical.architectureReference ?? "not recorded"}</dd>
              <dt>Decision reference</dt><dd>{selectedTechnical.decisionReference ?? "not recorded"}</dd>
              <dt>Evaluation reference</dt><dd>{selectedTechnical.evaluationReference ?? "not recorded"}</dd>
            </dl>
          ) : null}

          <dl className="detail-grid">
            <dt>Canonical ID</dt><dd><CanonicalId value={selected.item_id} /></dd>
            <dt>Publisher</dt><dd>{selected.publisher}</dd>
            <dt>Source</dt><dd>{selected.source.repository}</dd>
            <dt>Package</dt><dd>{selected.source.package_reference ?? "—"}</dd>
            <dt>Revision</dt><dd>{selected.source.revision ?? "unknown"}</dd>
            <dt>License</dt><dd>{selected.license}</dd>
            <dt>Provenance</dt><dd>{selected.provenance}</dd>
            <dt>Review reference</dt><dd>{selected.review_reference ?? "not recorded"}</dd>
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
                <p>
                  This listing is discovery/evaluation/reference metadata only and cannot be
                  activated automatically. Listing it does not mean the component is adopted.
                </p>
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
