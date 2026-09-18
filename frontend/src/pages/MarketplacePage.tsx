import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  RegistryClient,
  type RegistryDependency,
  type RegistryItem,
  type RegistryKindDescriptor,
  type RegistryMaturity,
  type RegistryPermissionChange,
  type RegistryPreview,
  type RegistryTrustStatus,
} from "../api/registry";
import type { JsonValue, ListQuery, Page } from "../api/types";
import {
  CanonicalId,
  Card,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";
import { technicalMetadata } from "../marketplace/technical";

const TRUST_STATES: RegistryTrustStatus[] = ["trusted", "reviewed", "local", "untrusted"];
const MATURITY_STATES: RegistryMaturity[] = ["stable", "beta", "experimental"];
const PAGE_SIZE = 24;

const PRIMARY_KIND_GROUPS: Array<{ value: string; label: string; kinds: string[] }> = [
  { value: "tool", label: "Tools", kinds: ["tool"] },
  { value: "skill", label: "Skills", kinds: ["skill"] },
  { value: "plugin", label: "Plugins", kinds: ["plugin"] },
  { value: "connector", label: "Connectors", kinds: ["connector"] },
  { value: "application", label: "Applications", kinds: ["application"] },
  { value: "template,workflow", label: "Templates / Workflows", kinds: ["template", "workflow"] },
];

const FALLBACK_KIND_DESCRIPTORS: RegistryKindDescriptor[] = [
  descriptor("tool", "Tool", "portable_import"),
  descriptor("skill", "Skill", "kind_handler"),
  descriptor("plugin", "Plugin", "plugin"),
  descriptor("connector", "Connector", "portable_import"),
  descriptor("application", "Application", "kind_handler", "/applications"),
  descriptor("template", "Template", "portable_import"),
  descriptor("workflow", "Workflow", "portable_import"),
];

function descriptor(
  kind: string,
  display_name: string,
  default_route: RegistryKindDescriptor["default_route"],
  management_path: string | null = null,
): RegistryKindDescriptor {
  return {
    kind,
    display_name,
    default_route,
    supports_install: default_route !== "manual",
    supports_update: default_route !== "manual",
    supports_uninstall: default_route === "kind_handler" || default_route === "plugin",
    management_path,
  };
}

export function MarketplacePage({ client }: { client: RegistryClient }) {
  const [page, setPage] = useState<Page<RegistryItem> | null>(null);
  const [kindDescriptors, setKindDescriptors] = useState<RegistryKindDescriptor[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [queryText, setQueryText] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [trustStatus, setTrustStatus] = useState<RegistryTrustStatus | "">("");
  const [maturity, setMaturity] = useState<RegistryMaturity | "">("");
  const [tag, setTag] = useState("");
  const [category, setCategory] = useState("");
  const [license, setLicense] = useState("");
  const [publisher, setPublisher] = useState("");
  const [sourceRegistry, setSourceRegistry] = useState("");
  const [requiredCapability, setRequiredCapability] = useState("");
  const [platformVersion, setPlatformVersion] = useState("");
  const [installedFilter, setInstalledFilter] = useState<"" | "true" | "false">("");
  const [compatibilityFilter, setCompatibilityFilter] = useState<"" | "true" | "false">("");
  const [technicalOnly, setTechnicalOnly] = useState(false);
  const [updatesOnly, setUpdatesOnly] = useState(false);
  const [sort, setSort] = useState("name");
  const [direction, setDirection] = useState<"asc" | "desc">("asc");
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [selected, setSelected] = useState<RegistryItem | null>(null);
  const [preview, setPreview] = useState<RegistryPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const listQuery = useMemo<ListQuery>(() => {
    const filters: Record<string, string> = {};
    if (kindFilter) filters.item_type = kindFilter;
    if (trustStatus) filters.trust_status = trustStatus;
    if (maturity) filters.maturity = maturity;
    if (tag.trim()) filters.tag = tag.trim();
    if (category.trim()) filters.category = category.trim();
    if (license.trim()) filters.license = license.trim();
    if (publisher.trim()) filters.publisher = publisher.trim();
    if (sourceRegistry.trim()) filters.source = sourceRegistry.trim();
    if (requiredCapability.trim()) filters.required_capability = requiredCapability.trim();
    if (platformVersion.trim()) filters.platform_version = platformVersion.trim();
    if (installedFilter) filters.installed = installedFilter;
    if (compatibilityFilter) filters.compatible = compatibilityFilter;
    if (technicalOnly) filters.technical_component = "true";
    if (updatesOnly) filters.update_available = "true";
    return {
      limit: PAGE_SIZE,
      sort,
      direction,
      q: queryText.trim() || undefined,
      filters,
    };
  }, [
    category,
    compatibilityFilter,
    direction,
    installedFilter,
    kindFilter,
    license,
    maturity,
    platformVersion,
    publisher,
    queryText,
    sourceRegistry,
    requiredCapability,
    sort,
    tag,
    technicalOnly,
    trustStatus,
    updatesOnly,
  ]);

  const load = useCallback(
    async (pageCursor?: string) => {
      try {
        const [next, registeredKinds] = await Promise.all([
          client.list({ ...listQuery, cursor: pageCursor }),
          client.listKinds({ limit: 200, sort: "kind", direction: "asc" }),
        ]);
        setPage(next);
        setKindDescriptors(registeredKinds.items);
        setError(null);
      } catch (nextError) {
        setError(nextError);
      }
    },
    [client, listQuery],
  );

  useEffect(() => {
    setCursor(undefined);
    setCursorHistory([]);
    void load();
  }, [load]);

  const effectiveKindDescriptors = useMemo(
    () => mergeKindDescriptors(
      kindDescriptors,
      [...(page?.items ?? []), ...(selected ? [selected] : [])],
    ),
    [kindDescriptors, page, selected],
  );

  const dynamicKinds = useMemo(() => {
    const primary = new Set(PRIMARY_KIND_GROUPS.flatMap((entry) => entry.kinds));
    return effectiveKindDescriptors
      .filter((entry) => !primary.has(entry.kind))
      .sort((left, right) => kindLabel(left).localeCompare(kindLabel(right)));
  }, [effectiveKindDescriptors]);

  const selectedKind = selected
    ? effectiveKindDescriptors.find((entry) => entry.kind === selected.item_type) ??
      fallbackDescriptor(selected.item_type, selected.route)
    : null;

  const selectItem = async (item: RegistryItem) => {
    setSelected(item);
    setPreview(null);
    setActionError(null);
    setSuccessMessage(null);
    setDetailLoading(true);
    try {
      const detail = await client.get(item.item_id, item.version, item.source_registry);
      setSelected(detail);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setDetailLoading(false);
    }
  };

  const runPreview = async (item: RegistryItem) => {
    setBusy(true);
    setActionError(null);
    setSuccessMessage(null);
    try {
      const next = await client.preview(
        item.item_id,
        item.version,
        undefined,
        item.source_registry,
      );
      setPreview(next);
      setSelected((current) => ({
        ...next.item,
        owner_extension: next.item.owner_extension ?? current?.owner_extension ?? null,
        manifest_reference:
          next.item.manifest_reference ?? current?.manifest_reference ?? current?.manifest ?? null,
      }));
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const applyInstallOrUpdate = async () => {
    if (!selected || !preview || preview.id !== selected.id || !preview.activation_allowed) return;
    const operation = mutationOperation(selected);
    if (!operation || !operationSupported(selected, operation)) return;
    setBusy(true);
    setActionError(null);
    setSuccessMessage(null);
    try {
      if (operation === "update") {
        await client.update(
          selected.item_id,
          selected.version,
          undefined,
          selected.source_registry,
        );
      } else {
        await client.install(
          selected.item_id,
          selected.version,
          undefined,
          selected.source_registry,
        );
      }
      setSuccessMessage(operation === "update" ? "Update applied." : "Component installed.");
      await refreshSelected(selected);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const uninstall = async () => {
    if (!selected || !uninstallSupported(selected)) return;
    setBusy(true);
    setActionError(null);
    setSuccessMessage(null);
    try {
      await client.uninstall(selected.item_id);
      setSuccessMessage("Component uninstalled.");
      await refreshSelected(selected);
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
    setSuccessMessage(null);
    try {
      await client.pin(selected.item_id, selected.installed_version);
      setSuccessMessage("Installed version pinned.");
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
    setSuccessMessage(null);
    try {
      await client.unpin(selected.item_id);
      setSuccessMessage("Version pin removed.");
      await refreshSelected(selected);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const refreshSelected = async (item: RegistryItem) => {
    const refreshed = await client.get(item.item_id, item.version, item.source_registry);
    setSelected(refreshed);
    setPreview(null);
    await load(cursor);
  };

  const resetFilters = (event: FormEvent) => {
    event.preventDefault();
    setQueryText("");
    setKindFilter("");
    setTrustStatus("");
    setMaturity("");
    setTag("");
    setCategory("");
    setLicense("");
    setPublisher("");
    setSourceRegistry("");
    setRequiredCapability("");
    setPlatformVersion("");
    setInstalledFilter("");
    setCompatibilityFilter("");
    setTechnicalOnly(false);
    setUpdatesOnly(false);
    setSort("name");
    setDirection("asc");
  };

  const goNext = () => {
    if (!page?.next_cursor) return;
    setCursorHistory((history) => [...history, cursor ?? ""]);
    setCursor(page.next_cursor);
    void load(page.next_cursor);
  };

  const goBack = () => {
    if (cursorHistory.length === 0) return;
    const previous = cursorHistory[cursorHistory.length - 1] ?? "";
    setCursorHistory((history) => history.slice(0, -1));
    const previousCursor = previous || undefined;
    setCursor(previousCursor);
    void load(previousCursor);
  };

  const filtersActive =
    Boolean(queryText.trim() || kindFilter || trustStatus || maturity || tag.trim() || category.trim()) ||
    Boolean(
      license.trim() ||
      publisher.trim() ||
      sourceRegistry.trim() ||
      requiredCapability.trim() ||
      platformVersion.trim()
    ) ||
    Boolean(installedFilter || compatibilityFilter || technicalOnly || updatesOnly);

  const providerDisabled = error ? providerLooksDisabled(error) : false;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Unified component catalog</p>
        <h1>Marketplace</h1>
        <p>
          Discover Tools, Skills, Plugins, Connectors, Applications, Templates, Workflows and
          future component kinds in one catalog. Mutations remain delegated to the canonical
          owner domain for each component.
        </p>
      </header>

      <Card title="Component kinds">
        <div className="button-row" aria-label="Marketplace component kinds">
          <button type="button" aria-pressed={!kindFilter} onClick={() => setKindFilter("")}>
            All
          </button>
          {PRIMARY_KIND_GROUPS.map((entry) => (
            <button
              type="button"
              key={entry.value}
              aria-pressed={kindFilter === entry.value}
              onClick={() => setKindFilter(entry.value)}
            >
              {entry.label}
            </button>
          ))}
          {dynamicKinds.map((entry) => (
            <button
              type="button"
              key={entry.kind}
              aria-pressed={kindFilter === entry.kind}
              onClick={() => setKindFilter(entry.kind)}
            >
              {kindLabel(entry)}
            </button>
          ))}
        </div>
        <p>
          Known navigation groups are stable presentation shortcuts. Additional valid kinds are
          discovered from Registry results and rendered generically.
        </p>
      </Card>

      <Card title="Discover">
        <form className="toolbar" onSubmit={resetFilters}>
          <label>
            Component kind
            <input
              value={kindFilter}
              placeholder="tool or future_kind"
              onChange={(event) => setKindFilter(event.target.value)}
            />
          </label>
          <label>
            Search
            <input
              type="search"
              value={queryText}
              placeholder="Name, capability, publisher…"
              onChange={(event) => setQueryText(event.target.value)}
            />
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
            Maturity
            <select
              value={maturity}
              onChange={(event) => setMaturity(event.target.value as RegistryMaturity | "")}
            >
              <option value="">All maturity levels</option>
              {MATURITY_STATES.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Installed state
            <select
              value={installedFilter}
              onChange={(event) => setInstalledFilter(event.target.value as "" | "true" | "false")}
            >
              <option value="">All install states</option>
              <option value="true">Installed</option>
              <option value="false">Not installed</option>
            </select>
          </label>
          <label>
            Compatibility
            <select
              value={compatibilityFilter}
              onChange={(event) =>
                setCompatibilityFilter(event.target.value as "" | "true" | "false")
              }
            >
              <option value="">All compatibility states</option>
              <option value="true">Compatible</option>
              <option value="false">Incompatible</option>
            </select>
          </label>
          <label>
            Tags
            <input value={tag} placeholder="tag" onChange={(event) => setTag(event.target.value)} />
          </label>
          <label>
            Category
            <input
              value={category}
              placeholder="category"
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
            Marketplace source
            <input
              value={sourceRegistry}
              placeholder="official or private"
              onChange={(event) => setSourceRegistry(event.target.value)}
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
          <label>
            Sort
            <select value={sort} onChange={(event) => setSort(event.target.value)}>
              <option value="name">Name</option>
              <option value="version">Version</option>
              <option value="publisher">Publisher</option>
              <option value="source_registry">Marketplace source</option>
              <option value="released_at">Release date</option>
              <option value="item_type">Kind</option>
              <option value="maturity">Maturity</option>
            </select>
          </label>
          <label>
            Direction
            <select
              value={direction}
              onChange={(event) => setDirection(event.target.value as "asc" | "desc")}
            >
              <option value="asc">Ascending</option>
              <option value="desc">Descending</option>
            </select>
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

        {error ? (
          <div className="stack">
            <DegradedState
              title={providerDisabled ? "Marketplace provider disabled" : "Marketplace provider unavailable"}
              detail={
                providerDisabled
                  ? "This deployment does not expose the Registry Marketplace resource."
                  : "The catalog provider could not be read. Existing component runtimes remain separate from Marketplace availability."
              }
            />
            <ErrorState error={error} onRetry={() => void load(cursor)} />
          </div>
        ) : null}
        {!page && !error ? <LoadingState label="Loading Marketplace…" /> : null}
        {page && page.items.length === 0 ? (
          <EmptyState
            title={filtersActive ? "No Marketplace items match" : "Marketplace is empty"}
            detail={
              filtersActive
                ? "Change the search or filters. Filtering and sorting are executed by the server."
                : "Configure a Marketplace source or provider containing discoverable components."
            }
          />
        ) : null}
        {page && page.items.length > 0 ? (
          <div className="stack">
            <p>
              Showing {page.items.length} item{page.items.length === 1 ? "" : "s"}
              {page.total >= 0 ? ` of ${page.total}` : ""}.
            </p>
            <div className="card-grid">
              {page.items.map((item) => (
                <MarketplaceItemCard
                  key={item.qualified_id ?? item.id}
                  item={item}
                  descriptor={
                    effectiveKindDescriptors.find((entry) => entry.kind === item.item_type) ??
                    fallbackDescriptor(item.item_type, item.route)
                  }
                  onInspect={() => void selectItem(item)}
                />
              ))}
            </div>
            <div className="button-row" aria-label="Marketplace pagination">
              <button type="button" disabled={cursorHistory.length === 0} onClick={goBack}>
                Previous page
              </button>
              <button type="button" disabled={!page.next_cursor} onClick={goNext}>
                Next page
              </button>
            </div>
          </div>
        ) : null}
      </Card>

      {detailLoading ? <LoadingState label="Loading Marketplace details…" /> : null}
      {selected ? (
        <MarketplaceDetail
          item={selected}
          descriptor={selectedKind ?? fallbackDescriptor(selected.item_type, selected.route)}
          preview={preview}
          busy={busy}
          actionError={actionError}
          successMessage={successMessage}
          onPreview={() => void runPreview(selected)}
          onApply={() => void applyInstallOrUpdate()}
          onUninstall={() => void uninstall()}
          onPin={() => void pin()}
          onUnpin={() => void unpin()}
        />
      ) : null}
    </div>
  );
}

function MarketplaceItemCard({
  item,
  descriptor,
  onInspect,
}: {
  item: RegistryItem;
  descriptor: RegistryKindDescriptor;
  onInspect: () => void;
}) {
  const technical = technicalMetadata(item);
  const compatible = platformCompatible(item);
  return (
    <article className="card">
      <div className="button-row">
        <StatusBadge value={kindLabel(descriptor)} />
        <StatusBadge value={item.trust_status} />
        {compatible === false ? <StatusBadge value="incompatible" /> : null}
        {candidateIsInstalled(item) ? <StatusBadge value="installed" /> : null}
        {item.update_available ? <StatusBadge value="update_available" /> : null}
        {item.pinned_version ? <StatusBadge value="pinned" /> : null}
        {item.operation_state && item.operation_state !== "ready" ? (
          <StatusBadge value={item.operation_state} />
        ) : null}
        {missingHandler(item) ? <StatusBadge value="missing_handler" /> : null}
      </div>
      <h2>{item.name}</h2>
      <p>{item.description || "No description provided."}</p>
      {technical ? <p>{technical.categories.join(" · ")}</p> : null}
      <dl className="detail-grid">
        <dt>Publisher</dt><dd>{item.publisher || "unknown"}</dd>
        <dt>Version</dt><dd>{item.version}</dd>
        <dt>Source</dt><dd>{item.source?.repository || "not recorded"}</dd>
        <dt>State</dt><dd>{itemStateLabel(item)}</dd>
      </dl>
      <button type="button" onClick={onInspect}>Inspect</button>
    </article>
  );
}

function MarketplaceDetail({
  item,
  descriptor,
  preview,
  busy,
  actionError,
  successMessage,
  onPreview,
  onApply,
  onUninstall,
  onPin,
  onUnpin,
}: {
  item: RegistryItem;
  descriptor: RegistryKindDescriptor;
  preview: RegistryPreview | null;
  busy: boolean;
  actionError: unknown;
  successMessage: string | null;
  onPreview: () => void;
  onApply: () => void;
  onUninstall: () => void;
  onPin: () => void;
  onUnpin: () => void;
}) {
  const operation = mutationOperation(item);
  const supportsOperation = operation ? operationSupported(item, operation) : false;
  const selectedIsInstalledVersion = candidateIsInstalled(item);
  const partialMetadata =
    !item.publisher ||
    !item.license ||
    !item.provenance ||
    !item.source?.repository ||
    !item.released_at ||
    !item.changelog ||
    !item.integrity?.sha256;
  const managementPath =
    descriptor.management_path ?? (item.item_type === "application" ? "/applications" : null);
  const previewBlocked = preview !== null && !preview.activation_allowed;
  const handlerMissing =
    missingHandler(item) ||
    preview?.findings.some((finding) => finding.code.includes("handler")) === true;
  const manifest = item.manifest_reference ?? item.manifest;
  const compatible = platformCompatible(item);
  const ownerDetails = jsonRecord(item.owner_extension?.details);
  const ownerRequirements = item.owner_extension?.requirements;
  const ownerStatus = item.owner_extension?.status;

  return (
    <Card title={`${item.name} ${item.version}`}>
      <div className="button-row">
        <StatusBadge value={kindLabel(descriptor)} />
        <StatusBadge value={item.trust_status} />
        <StatusBadge value={item.route} />
        {compatible === false ? <StatusBadge value="incompatible" /> : null}
        {item.deprecated ? <StatusBadge value="deprecated" /> : null}
        {item.yanked ? <StatusBadge value="yanked" /> : null}
        {item.update_available ? <StatusBadge value="update_available" /> : null}
        {item.pinned_version ? <StatusBadge value="pinned" /> : null}
        {previewBlocked ? <StatusBadge value="blocked" /> : null}
        {handlerMissing ? <StatusBadge value="missing_handler" /> : null}
      </div>

      {partialMetadata ? (
        <DegradedState
          title="Partial metadata"
          detail="Some optional catalog metadata is absent. Missing presentation metadata does not broaden install authority."
        />
      ) : null}

      <p>{item.description || "No description provided."}</p>
      <dl className="detail-grid">
        <dt>Kind</dt><dd>{kindLabel(descriptor)}</dd>
        <dt>Canonical ID</dt><dd><CanonicalId value={item.item_id} /></dd>
        <dt>Publisher</dt><dd>{item.publisher || "unknown"}</dd>
        <dt>Version</dt><dd>{item.version}</dd>
        <dt>Source</dt><dd>{item.source?.repository || "not recorded"}</dd>
        <dt>Package</dt><dd>{item.source?.package_reference ?? "—"}</dd>
        <dt>Revision</dt><dd>{item.source?.revision ?? "unknown"}</dd>
        <dt>License</dt><dd>{item.license || "not recorded"}</dd>
        <dt>Provenance</dt><dd>{item.provenance || "not recorded"}</dd>
        <dt>Trust</dt><dd>{item.trust_status}</dd>
        <dt>Maturity</dt><dd>{item.maturity ?? item.stability ?? "not recorded"}</dd>
        <dt>Review reference</dt><dd>{item.review_reference ?? "not recorded"}</dd>
        <dt>SHA-256</dt><dd>{item.integrity?.sha256 ?? "not declared"}</dd>
        <dt>Signature</dt>
        <dd>
          {item.integrity?.signature_present
            ? `present (${item.integrity.signature_key_id ?? "no key ID"})`
            : "not declared"}
        </dd>
        <dt>Supported platform</dt>
        <dd>{item.minimum_platform_version ?? "any"} – {item.maximum_platform_version ?? "any"}</dd>
        <dt>Compatibility</dt>
        <dd>{compatible === null ? "not evaluated" : compatible ? "compatible" : "incompatible"}</dd>
        <dt>Installed version</dt><dd>{item.installed_version ?? "not installed"}</dd>
        <dt>Installed source</dt><dd>{item.installed_source_registry ?? "not installed"}</dd>
        <dt>Pinned version</dt><dd>{item.pinned_version ?? "not pinned"}</dd>
        <dt>Categories</dt><dd>{item.categories.join(", ") || "none"}</dd>
        <dt>Tags</dt><dd>{item.tags.join(", ") || "none"}</dd>
      </dl>

      {item.compatibility?.reasons?.length ? (
        <RequirementList title="Compatibility findings" values={item.compatibility.reasons} />
      ) : null}
      <RequirementList title="Operating systems" values={item.compatibility?.operating_systems ?? []} />
      <RequirementList title="Architectures" values={item.compatibility?.architectures ?? []} />
      <RequirementList title="Required runtimes" values={item.compatibility?.required_runtimes ?? []} />
      <RequirementList title="Missing runtimes" values={item.compatibility?.missing_runtimes ?? []} />
      <RequirementList title="Missing capabilities" values={item.compatibility?.missing_capabilities ?? []} />
      <RequirementList title="Missing plugins" values={item.compatibility?.missing_plugins ?? []} />
      <RequirementList title="Missing connectors" values={item.compatibility?.missing_connectors ?? []} />
      <RequirementList title="Missing models" values={item.compatibility?.missing_models ?? []} />
      <DependencyList dependencies={item.dependencies} />
      <RequirementList title="Requested permissions" values={item.requested_permissions} />
      <RequirementList title="Required capabilities" values={item.required_capabilities} />
      <RequirementList title="Required plugins" values={item.required_plugins} />
      <RequirementList title="Required connectors" values={item.required_connectors} />
      <RequirementList title="Required models" values={item.required_models} />

      {manifest ? (
        <div>
          <h3>Definition / manifest</h3>
          <dl className="detail-grid">
            <dt>Kind</dt><dd>{humanizeKind(manifest.kind)}</dd>
            <dt>Reference</dt><dd>{manifest.reference}</dd>
            <dt>Schema</dt><dd>{manifest.schema_version ?? "not recorded"}</dd>
          </dl>
        </div>
      ) : null}

      {ownerRequirements !== undefined && ownerRequirements !== null ? (
        <MetadataValue title="Owner requirements" value={ownerRequirements} />
      ) : null}
      {ownerDetails ? <KindSpecificDetails title="Kind-specific details" values={ownerDetails} /> : null}
      {ownerStatus !== undefined && ownerStatus !== null ? (
        <MetadataValue title="Owner status" value={ownerStatus} />
      ) : null}
      {item.kind_details ? <KindSpecificDetails title="Kind-specific details" values={item.kind_details} /> : null}

      {item.changelog ? (
        <div>
          <h3>Changelog</h3>
          <p>{item.changelog}</p>
        </div>
      ) : null}

      {item.update_available && item.pinned_version ? (
        <DegradedState
          title="Update blocked by version pin"
          detail="A newer version is discoverable, but the installed version is pinned. Unpin before updating."
        />
      ) : null}

      {item.route === "manual" || item.operation_state === "manual" ? (
        <DegradedState
          title="Manual component"
          detail="This item is discoverable but does not support automatic Marketplace mutation."
        />
      ) : null}
      {operation && !supportsOperation && !item.pinned_version && item.route !== "manual" ? (
        <DegradedState
          title={operation === "update" ? "Update unsupported" : "Install unsupported"}
          detail="The canonical owner does not advertise this Marketplace mutation for the component."
        />
      ) : null}
      {item.route_available === false && item.route !== "manual" && !handlerMissing ? (
        <DegradedState
          title="Operation unsupported"
          detail="The catalog item is valid, but no automatic distribution route is available in this deployment."
        />
      ) : null}
      {handlerMissing ? (
        <DegradedState
          title="Owner handler unavailable"
          detail="The catalog item is valid, but the canonical owner-domain handler required for mutation is not available."
        />
      ) : null}

      {item.item_type === "application" ? (
        <div>
          <h3>Application ownership</h3>
          <p>
            Marketplace owns discovery, definition inspection and install/update delegation only.
            Start, stop, restart, health and runtime lifecycle remain in Applications.
          </p>
          <a href={managementPath ?? "/applications"}>Open Application management</a>
        </div>
      ) : managementPath ? (
        <p><a href={managementPath}>Open canonical management</a></p>
      ) : null}

      <div className="button-row">
        {operation && supportsOperation ? (
          <button type="button" disabled={busy} onClick={onPreview}>
            {busy ? "Operation pending…" : operation === "update" ? "Preview update" : "Preview install"}
          </button>
        ) : null}
        {candidateIsInstalled(item) && !item.pinned_version ? (
          <button type="button" disabled={busy} onClick={onPin}>Pin installed version</button>
        ) : null}
        {candidateIsInstalled(item) && item.pinned_version ? (
          <button type="button" disabled={busy} onClick={onUnpin}>Unpin</button>
        ) : null}
        {uninstallSupported(item) ? (
          <button type="button" disabled={busy} onClick={onUninstall}>Uninstall</button>
        ) : null}
      </div>

      {busy ? <LoadingState label="Marketplace operation pending…" /> : null}
      {successMessage ? <div className="state" role="status"><strong>{successMessage}</strong></div> : null}
      {actionError ? <ErrorState error={actionError} /> : null}

      {preview ? (
        <InstallPreview
          preview={preview}
          item={item}
          operation={operation}
          selectedIsInstalledVersion={selectedIsInstalledVersion}
          busy={busy}
          onApply={onApply}
        />
      ) : null}
    </Card>
  );
}

function InstallPreview({
  preview,
  item,
  operation,
  selectedIsInstalledVersion,
  busy,
  onApply,
}: {
  preview: RegistryPreview;
  item: RegistryItem;
  operation: "install" | "update" | null;
  selectedIsInstalledVersion: boolean;
  busy: boolean;
  onApply: () => void;
}) {
  const decision = preview.decision ?? null;
  const dependencies = preview.dependencies ?? item.dependencies;
  const permissionChanges = [
    ...(preview.permission_changes ?? []),
    ...(decision
      ? [
          ...decision.permission_diff.added.map((permission) => ({
            permission,
            change: "added" as const,
            previous: null,
            next: "requested",
          })),
          ...decision.permission_diff.removed.map((permission) => ({
            permission,
            change: "removed" as const,
            previous: "granted",
            next: null,
          })),
        ]
      : []),
  ];
  const structuredMissing = decision
    ? [
        ...decision.compatibility.missing_runtimes.map((value) => `runtime: ${value}`),
        ...decision.compatibility.missing_capabilities.map((value) => `capability: ${value}`),
        ...decision.compatibility.missing_plugins.map((value) => `plugin: ${value}`),
        ...decision.compatibility.missing_connectors.map((value) => `connector: ${value}`),
        ...decision.compatibility.missing_models.map((value) => `model: ${value}`),
      ]
    : [];
  const structuredIncompatible = decision
    ? [
        ...(decision.compatibility.platform_compatible ? [] : ["platform version"]),
        ...(decision.compatibility.operating_system_compatible ? [] : ["operating system"]),
        ...(decision.compatibility.architecture_compatible ? [] : ["architecture"]),
      ]
    : [];
  const blockers = [
    ...(preview.blockers ?? []),
    ...preview.findings.filter((finding) => finding.severity === "error").map((finding) => finding.message),
  ];
  const sourceChanged =
    preview.source_changed ??
    knownValueChanged(
      decision?.provenance_diff.previous_source_registry,
      decision?.provenance_diff.candidate_source_registry,
    );
  const publisherChanged =
    preview.publisher_changed ??
    knownValueChanged(
      decision?.provenance_diff.previous_publisher,
      decision?.provenance_diff.candidate_publisher,
    );
  const approvalRequired = preview.approval_required ?? decision?.approval.required ?? false;
  const approvalReasons = decision?.approval.reasons ?? [];
  const securityNotices = [
    ...(preview.security_notices ?? []),
    ...(decision?.update_state.trust_integrity_issue
      ? ["Trust or integrity state changed for this candidate."]
      : []),
  ];
  const integrityNotices = [
    ...(preview.integrity_notices ?? []),
    ...provenanceIntegrityChanges(decision?.provenance_diff),
  ];
  const canApply =
    Boolean(operation) &&
    operation !== null &&
    operationSupported(item, operation) &&
    preview.activation_allowed &&
    !item.pinned_version &&
    !decision?.update_state.blocked_by_pin;

  return (
    <div className="stack">
      <h3>Install / update preview</h3>
      <p>
        Provider <strong>{preview.provider_id}</strong>. Mutation is
        {preview.activation_allowed ? " allowed" : " blocked"} after server-side checks.
      </p>

      {decision?.dependencies.length ? (
        <DecisionDependencyList dependencies={decision.dependencies} />
      ) : (
        <DependencyList dependencies={dependencies} />
      )}
      <RequirementList
        title="Deterministic install order"
        values={(decision?.install_order ?? []).map(
          (step, index) =>
            `${index + 1}. ${step.item_id}@${step.version} · ${humanizeKind(step.item_kind)} · ${step.source_registry ?? "unknown source"}`,
        )}
      />
      <RequirementList
        title="Missing requirements"
        values={[...(preview.missing_requirements ?? []), ...structuredMissing]}
      />
      <RequirementList
        title="Incompatible requirements"
        values={[...(preview.incompatible_requirements ?? []), ...structuredIncompatible]}
      />
      <PermissionDiff changes={dedupePermissionChanges(permissionChanges)} />

      {sourceChanged ? (
        <DegradedState title="Source changed" detail="The candidate source differs from the installed component." />
      ) : null}
      {publisherChanged ? (
        <DegradedState title="Publisher changed" detail="The candidate publisher differs from the installed component." />
      ) : null}
      {decision ? <ProvenanceDiffView diff={decision.provenance_diff} /> : null}
      <RequirementList title="Security notices" values={securityNotices} />
      <RequirementList title="Integrity notices" values={integrityNotices} />
      <RequirementList title="Blockers" values={blockers} />

      {decision?.dependency_blocked ? (
        <DegradedState
          title="Dependency plan blocked"
          detail="One or more required Marketplace dependencies cannot be satisfied for this operation."
        />
      ) : null}
      {decision?.update_state.blocked_by_pin ? (
        <DegradedState
          title="Update blocked by version pin"
          detail="The decision engine reports that the candidate cannot replace the pinned version."
        />
      ) : null}
      {decision?.update_state.incompatible_update ? (
        <DegradedState
          title="Incompatible update"
          detail="A newer candidate exists, but it is not compatible with the current platform environment."
        />
      ) : null}
      {approvalRequired ? (
        <DegradedState
          title="Approval required"
          detail={
            approvalReasons.length > 0
              ? approvalReasons.join("; ")
              : preview.approval_reference
                ? `Approval ${preview.approval_reference} is required before mutation.`
                : "Platform policy requires approval before mutation."
          }
        />
      ) : null}

      {preview.findings.length === 0 ? (
        <div className="state"><strong>No validation findings</strong></div>
      ) : (
        <ul>
          {preview.findings.map((finding, index) => (
            <li key={`${finding.severity}:${finding.code}:${index}`}>
              <StatusBadge value={finding.severity} /> {finding.code}: {finding.message}
            </li>
          ))}
        </ul>
      )}

      {preview.kind_details ? (
        <KindSpecificDetails title="Owner-specific preview details" values={preview.kind_details} />
      ) : null}

      {canApply && !selectedIsInstalledVersion ? (
        <button type="button" disabled={busy} onClick={onApply}>
          {operation === "update" ? "Apply update" : "Install component"}
        </button>
      ) : null}
      {selectedIsInstalledVersion ? <p>This exact version is already installed.</p> : null}
      {!preview.activation_allowed ? <p>Resolve the blockers above before retrying.</p> : null}
    </div>
  );
}

function DecisionDependencyList({
  dependencies,
}: {
  dependencies: NonNullable<RegistryPreview["decision"]>["dependencies"];
}) {
  return (
    <div>
      <h3>Dependencies</h3>
      <ul>
        {dependencies.map((dependency, index) => (
          <li key={`${dependency.required_by}:${dependency.item_id}:${index}`}>
            {dependency.item_kind ? `${humanizeKind(dependency.item_kind)} · ` : ""}
            <CanonicalId value={dependency.item_id} /> — {dependency.optional ? "optional" : "required"}
            {" · "}<StatusBadge value={dependency.status} />
            {dependency.blocking ? <> · <StatusBadge value="blocking" /></> : null}
            {dependency.installed_version ? ` · installed ${dependency.installed_version}` : ""}
            {dependency.candidate_version ? ` · candidate ${dependency.candidate_version}` : ""}
            {dependency.path.length > 1 ? ` · path ${dependency.path.join(" → ")}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ProvenanceDiffView({
  diff,
}: {
  diff: NonNullable<RegistryPreview["decision"]>["provenance_diff"];
}) {
  if (!diff.installed) return null;
  const changes = [
    changedPair("Repository", diff.previous_repository, diff.candidate_repository),
    changedPair("Package", diff.previous_package_reference, diff.candidate_package_reference),
    changedPair("Revision", diff.previous_revision, diff.candidate_revision),
    changedPair("Artifact SHA-256", diff.previous_artifact_sha256, diff.candidate_artifact_sha256),
    diff.signature_changed ? "Signature changed" : null,
    changedPair("Signature key", diff.previous_signature_key_id, diff.candidate_signature_key_id),
    changedPair("Trust", diff.previous_trust_status, diff.candidate_trust_status),
    changedPair("Review reference", diff.previous_review_reference, diff.candidate_review_reference),
  ].filter((value): value is string => value !== null);
  return <RequirementList title="Provenance changes" values={changes} />;
}

function changedPair(
  label: string,
  previous: string | null | undefined,
  candidate: string | null | undefined,
): string | null {
  if (previous === undefined || previous === null || previous === candidate) return null;
  return `${label}: ${previous || "none"} → ${candidate || "none"}`;
}

function knownValueChanged(
  previous: string | null | undefined,
  candidate: string | null | undefined,
): boolean {
  return previous !== undefined && previous !== null && candidate !== undefined && previous !== candidate;
}

function provenanceIntegrityChanges(
  diff: NonNullable<RegistryPreview["decision"]>["provenance_diff"] | undefined,
): string[] {
  if (!diff?.installed) return [];
  return [
    changedPair("Artifact digest", diff.previous_artifact_sha256, diff.candidate_artifact_sha256),
    diff.signature_changed ? "Signature changed" : null,
    changedPair("Signature key", diff.previous_signature_key_id, diff.candidate_signature_key_id),
  ].filter((value): value is string => value !== null);
}

function dedupePermissionChanges(changes: RegistryPermissionChange[]): RegistryPermissionChange[] {
  const seen = new Set<string>();
  return changes.filter((change) => {
    const key = `${change.permission}:${change.change}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function PermissionDiff({ changes }: { changes: RegistryPermissionChange[] }) {
  if (changes.length === 0) return null;
  return (
    <div>
      <h3>Permission changes</h3>
      <ul>
        {changes.map((change, index) => (
          <li key={`${change.permission}:${change.change}:${index}`}>
            <StatusBadge value={change.change} /> {change.permission}
            {change.previous || change.next
              ? ` — ${change.previous ?? "none"} → ${change.next ?? "none"}`
              : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RequirementList({ title, values }: { title: string; values: string[] }) {
  if (values.length === 0) return null;
  return (
    <div>
      <h3>{title}</h3>
      <ul>{values.map((value, index) => <li key={`${value}:${index}`}>{value}</li>)}</ul>
    </div>
  );
}

function DependencyList({ dependencies }: { dependencies: RegistryDependency[] }) {
  if (dependencies.length === 0) return null;
  return (
    <div>
      <h3>Dependencies</h3>
      <ul>
        {dependencies.map((dependency, index) => (
          <li key={`${dependency.item_id}:${index}`}>
            {dependency.item_kind ?? dependency.kind
              ? `${humanizeKind(dependency.item_kind ?? dependency.kind ?? "")} · `
              : ""}
            <CanonicalId value={dependency.item_id} /> — {dependency.optional ? "optional" : "required"};
            versions {dependency.minimum_version ?? "any"} – {dependency.maximum_version ?? "any"}
            {dependency.status ? <> · <StatusBadge value={dependency.status} /></> : null}
            {dependency.installed_version ? ` · installed ${dependency.installed_version}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function KindSpecificDetails({
  title,
  values,
}: {
  title: string;
  values: Record<string, JsonValue>;
}) {
  const entries = Object.entries(values);
  if (entries.length === 0) return null;
  return (
    <div>
      <h3>{title}</h3>
      <dl className="detail-grid">
        {entries.map(([key, value]) => (
          <FragmentRow key={key} label={humanizeKind(key)} value={formatMetadataValue(value)} />
        ))}
      </dl>
    </div>
  );
}

function MetadataValue({ title, value }: { title: string; value: JsonValue }) {
  const record = jsonRecord(value);
  if (record) return <KindSpecificDetails title={title} values={record} />;
  return (
    <div>
      <h3>{title}</h3>
      <p>{formatMetadataValue(value)}</p>
    </div>
  );
}

function FragmentRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function formatMetadataValue(value: unknown): string {
  if (value === null || value === undefined) return "not recorded";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function jsonRecord(value: JsonValue | null | undefined): Record<string, JsonValue> | null {
  if (!value || Array.isArray(value) || typeof value !== "object") return null;
  return value as Record<string, JsonValue>;
}

function platformCompatible(item: RegistryItem): boolean | null {
  if (typeof item.compatibility?.platform_compatible === "boolean") {
    return item.compatibility.platform_compatible;
  }
  if (typeof item.compatibility?.compatible === "boolean") return item.compatibility.compatible;
  if (typeof item.compatible === "boolean") return item.compatible;
  return null;
}

function routeAvailable(item: RegistryItem): boolean {
  if (item.route === "manual") return false;
  if (item.route_available === false) return false;
  if (item.route === "kind_handler" && item.owner_extension?.handler_available === false) return false;
  return true;
}

function missingHandler(item: RegistryItem): boolean {
  return (
    item.operation_state === "missing_handler" ||
    (item.route === "kind_handler" &&
      (item.route_available === false || item.owner_extension?.handler_available === false))
  );
}

function operationSupported(item: RegistryItem, operation: "install" | "update"): boolean {
  if (!routeAvailable(item) || item.pinned_version) return false;
  const advertised = item.owner_extension?.supported_operations;
  if (advertised && !advertised.includes(operation)) return false;
  // The canonical Application owner intentionally exposes no artifact-version migration yet.
  // Fail closed until the owner explicitly advertises update support.
  if (operation === "update" && item.item_type === "application" && !advertised) return false;
  if (operation === "install") return !item.installed;
  return item.installed && (
    (item.update_available && item.installed_version !== item.version) ||
    sameVersionSourceSwitch(item)
  );
}

function uninstallSupported(item: RegistryItem): boolean {
  if (!candidateIsInstalled(item) || !["kind_handler", "plugin"].includes(item.route)) return false;
  if (item.owner_extension?.handler_available !== true) return false;
  const advertised = item.owner_extension.supported_operations;
  if (advertised && !advertised.includes("uninstall")) return false;
  return item.route_available !== false;
}

function itemStateLabel(item: RegistryItem): string {
  if (item.operation_state === "blocked") return "blocked";
  if (item.operation_state === "manual" || item.route === "manual") return "manual";
  if (missingHandler(item)) return "missing handler";
  if (item.pinned_version && item.update_available) return "update available · pinned";
  if (item.update_available) return "update available";
  if (sameVersionSourceSwitch(item)) return "source change available";
  if (candidateIsInstalled(item)) return "installed";
  if (item.installed) return "installed from another source";
  if (item.route_available === false) return "unsupported";
  return "available";
}

function mutationOperation(item: RegistryItem): "install" | "update" | null {
  if (!item.installed) return "install";
  if (
    (item.update_available && item.installed_version !== item.version) ||
    sameVersionSourceSwitch(item)
  ) {
    return "update";
  }
  return null;
}

function candidateIsInstalled(item: RegistryItem): boolean {
  if (!item.installed || item.installed_version !== item.version) return false;
  if (typeof item.installation_source_matches === "boolean") {
    return item.installation_source_matches;
  }
  if (item.installed_source_registry && item.source_registry) {
    return item.installed_source_registry === item.source_registry;
  }
  return true;
}

function sameVersionSourceSwitch(item: RegistryItem): boolean {
  return (
    item.installed &&
    item.installed_version === item.version &&
    !candidateIsInstalled(item) &&
    Boolean(item.source_registry)
  );
}

function mergeKindDescriptors(
  serverKinds: RegistryKindDescriptor[],
  items: RegistryItem[],
): RegistryKindDescriptor[] {
  const merged = new Map<string, RegistryKindDescriptor>();
  for (const entry of FALLBACK_KIND_DESCRIPTORS) merged.set(entry.kind, entry);
  for (const entry of serverKinds) merged.set(entry.kind, entry);
  for (const item of items) {
    if (!merged.has(item.item_type)) {
      merged.set(item.item_type, fallbackDescriptor(item.item_type, item.route));
    }
  }
  return [...merged.values()];
}

function fallbackDescriptor(kind: string, route: RegistryKindDescriptor["default_route"]): RegistryKindDescriptor {
  return descriptor(
    kind,
    humanizeKind(kind),
    route,
    kind === "application" ? "/applications" : null,
  );
}

function kindLabel(descriptor: RegistryKindDescriptor): string {
  return descriptor.display_name.trim() || humanizeKind(descriptor.kind);
}

function humanizeKind(value: string): string {
  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function providerLooksDisabled(error: unknown): boolean {
  const text = error instanceof Error ? error.message : String(error);
  const normalized = text.toLowerCase();
  return (
    normalized.includes("404") ||
    normalized.includes("not found") ||
    normalized.includes("not configured") ||
    normalized.includes("disabled")
  );
}

export const marketplacePresentation = {
  humanizeKind,
  itemStateLabel,
  mutationOperation,
  mergeKindDescriptors,
  operationSupported,
  uninstallSupported,
  platformCompatible,
};
