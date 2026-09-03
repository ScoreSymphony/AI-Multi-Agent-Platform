import { useCallback, useEffect, useState } from "react";
import type {
  CanonicalCapability,
  CanonicalCapabilityProvider,
  CanonicalCapabilityVersion,
} from "../api/capabilities";
import { ControlPlaneClient, prettyJson } from "../api/client";
import type { Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const CAPABILITY_QUERY_KEY = "capabilities:id:asc";
const PROVIDER_QUERY_KEY = "capability-providers:id:asc";

export function CapabilitiesPage({ client }: { client: ControlPlaneClient }) {
  const [capabilities, setCapabilities] = useState<Page<CanonicalCapability> | null>(null);
  const [providers, setProviders] = useState<Page<CanonicalCapabilityProvider> | null>(null);
  const [capabilityError, setCapabilityError] = useState<unknown>(null);
  const [providerError, setProviderError] = useState<unknown>(null);
  const capabilityPagination = useCursorPagination(CAPABILITY_QUERY_KEY);
  const providerPagination = useCursorPagination(PROVIDER_QUERY_KEY);

  const loadCapabilities = useCallback(async () => {
    try {
      setCapabilities(
        await client.listCapabilities({
          limit: 100,
          cursor: capabilityPagination.cursor,
          sort: "id",
          direction: "asc",
        }),
      );
      setCapabilityError(null);
    } catch (error) {
      setCapabilityError(error);
    }
  }, [capabilityPagination.cursor, client]);

  const loadProviders = useCallback(async () => {
    try {
      setProviders(
        await client.listCapabilityProviders({
          limit: 100,
          cursor: providerPagination.cursor,
          sort: "id",
          direction: "asc",
        }),
      );
      setProviderError(null);
    } catch (error) {
      setProviderError(error);
    }
  }, [client, providerPagination.cursor]);

  useEffect(() => {
    void loadCapabilities();
  }, [loadCapabilities]);

  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

  if (!capabilities && !providers && !capabilityError && !providerError) return <LoadingState />;

  const availableOnPage = capabilities?.items.filter((item) => item.available).length ?? "—";
  const healthyProvidersOnPage =
    providers?.items.filter((provider) => provider.available && provider.health === "healthy").length
    ?? "—";

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Capability Registry</p>
        <h1>Tools & capabilities</h1>
        <p>
          Backend-neutral capability definitions and public provider descriptors. Invocation remains
          governed by the canonical capability/authorization pipeline and is not duplicated here.
        </p>
      </header>

      <div className="metrics">
        <Metric label="Capabilities" value={capabilities?.total ?? "—"} />
        <Metric label="Available on page" value={availableOnPage} />
        <Metric label="Providers" value={providers?.total ?? "—"} />
        <Metric label="Healthy on page" value={healthyProvidersOnPage} />
      </div>

      <Card title="Canonical capabilities">
        {capabilityError ? (
          <ErrorState error={capabilityError} onRetry={() => void loadCapabilities()} />
        ) : null}
        {capabilities ? (
          <CapabilityTable capabilities={capabilities.items} />
        ) : capabilityError ? null : (
          <LoadingState />
        )}
        {capabilities ? (
          <PaginationControls
            page={capabilities}
            pageNumber={capabilityPagination.pageNumber}
            hasPrevious={capabilityPagination.hasPrevious}
            onPrevious={capabilityPagination.previous}
            onRefresh={() => void loadCapabilities()}
            onNext={() => capabilityPagination.next(capabilities.next_cursor)}
          />
        ) : null}
      </Card>

      <Card title="Capability providers">
        {providerError ? (
          <ErrorState error={providerError} onRetry={() => void loadProviders()} />
        ) : null}
        {providers ? (
          <ProviderTable providers={providers.items} />
        ) : providerError ? null : (
          <LoadingState />
        )}
        {providers ? (
          <PaginationControls
            page={providers}
            pageNumber={providerPagination.pageNumber}
            hasPrevious={providerPagination.hasPrevious}
            onPrevious={providerPagination.previous}
            onRefresh={() => void loadProviders()}
            onNext={() => providerPagination.next(providers.next_cursor)}
          />
        ) : null}
      </Card>
    </div>
  );
}

export function CapabilityDetailPage({
  client,
  capabilityId,
}: {
  client: ControlPlaneClient;
  capabilityId: string;
}) {
  const [capability, setCapability] = useState<CanonicalCapability | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setCapability(await client.getCapability(capabilityId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [capabilityId, client]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !capability) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!capability) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Capability</p>
          <h1>{capability.name}</h1>
          <code>{capability.id}</code>
        </div>
        <div className="detail-status">
          <StatusBadge value={capability.available ? "available" : "unavailable"} />
          <span>
            {capability.version_count} version{capability.version_count === 1 ? "" : "s"}
          </span>
        </div>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <Card title="Version contracts">
        <CapabilityVersionTable versions={capability.versions} />
      </Card>
      <Card title="Boundary">
        <p>
          Capability identity, safety and approval metadata are canonical. Provider-private tool IDs,
          clients and adapter internals are intentionally not exposed as primary UI state.
        </p>
      </Card>
    </div>
  );
}

export function CapabilityProviderDetailPage({
  client,
  providerId,
}: {
  client: ControlPlaneClient;
  providerId: string;
}) {
  const [provider, setProvider] = useState<CanonicalCapabilityProvider | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setProvider(await client.getCapabilityProvider(providerId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, providerId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !provider) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!provider) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Capability provider</p>
          <h1>{provider.id}</h1>
          <code>{provider.id}</code>
        </div>
        <div className="detail-status">
          <StatusBadge value={provider.health} />
          <StatusBadge value={provider.available ? "available" : "unavailable"} />
        </div>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <div className="grid-two">
        <Card title="Provider contract">
          <DefinitionList
            values={{
              provider_type: provider.provider_type,
              contract_version: provider.contract_version,
              operations: provider.supported_operations.join(", ") || "—",
              capabilities: provider.capabilities.length,
            }}
          />
        </Card>
        <Card title="Public limits & resources">
          <pre>{prettyJson({ limits: provider.limits, resources: provider.resources })}</pre>
        </Card>
      </div>
      <Card title="Declared provider capabilities">
        {provider.capabilities.length === 0 ? (
          <EmptyState title="No provider capabilities declared" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th><th>Kind</th><th>Version</th><th>Operations</th><th>Features</th>
                </tr>
              </thead>
              <tbody>
                {provider.capabilities.map((capability) => (
                  <tr key={`${capability.name}:${capability.version}`}>
                    <td>{capability.name}</td>
                    <td>{capability.kind}</td>
                    <td>{capability.version}</td>
                    <td>{capability.supported_operations.join(", ") || "—"}</td>
                    <td>{capability.features.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function CapabilityTable({ capabilities }: { capabilities: CanonicalCapability[] }) {
  if (capabilities.length === 0) return <EmptyState title="No canonical capabilities" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Capability</th><th>Versions</th><th>Availability</th><th>Safety</th><th>Side effects</th>
          </tr>
        </thead>
        <tbody>
          {capabilities.map((capability) => (
            <tr key={capability.id}>
              <td>
                <AppLink href={`/tools/${encodeURIComponent(capability.id)}`}>
                  {capability.name}
                </AppLink>
                <div><code>{capability.id}</code></div>
              </td>
              <td>{capability.version_count}</td>
              <td><StatusBadge value={capability.available ? "available" : "unavailable"} /></td>
              <td>{commonVersionValue(capability.versions, "safety")}</td>
              <td>{commonVersionValue(capability.versions, "side_effects")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProviderTable({ providers }: { providers: CanonicalCapabilityProvider[] }) {
  if (providers.length === 0) return <EmptyState title="No capability providers" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Provider</th><th>Type</th><th>Health</th><th>Available</th><th>Capabilities</th></tr>
        </thead>
        <tbody>
          {providers.map((provider) => (
            <tr key={provider.id}>
              <td>
                <AppLink href={`/tools/providers/${encodeURIComponent(provider.id)}`}>
                  {provider.id}
                </AppLink>
              </td>
              <td>{provider.provider_type}</td>
              <td><StatusBadge value={provider.health} /></td>
              <td>{provider.available ? "yes" : "no"}</td>
              <td>{provider.capabilities.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CapabilityVersionTable({ versions }: { versions: CanonicalCapabilityVersion[] }) {
  if (versions.length === 0) return <EmptyState title="No capability versions" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Version</th><th>Health</th><th>Safety</th><th>Side effects</th><th>Permissions</th><th>Approvals</th><th>Input schema</th>
          </tr>
        </thead>
        <tbody>
          {versions.map((version) => (
            <tr key={version.version}>
              <td>{version.version}</td>
              <td><StatusBadge value={version.health} /></td>
              <td>{version.safety}</td>
              <td>{version.side_effects}</td>
              <td>{version.required_permissions.join(", ") || "—"}</td>
              <td>{version.required_approvals.join(", ") || "—"}</td>
              <td>{schemaSummary(version.input_schema)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function commonVersionValue(
  versions: CanonicalCapabilityVersion[],
  field: "safety" | "side_effects",
): string {
  const values = new Set(versions.map((version) => version[field]));
  if (values.size === 0) return "—";
  if (values.size > 1) return "mixed";
  return values.values().next().value ?? "—";
}

function schemaSummary(schema: Record<string, unknown>): string {
  const rawProperties = schema.properties;
  if (typeof rawProperties === "object" && rawProperties !== null) {
    const fieldCount = Object.keys(rawProperties).length;
    return `${fieldCount} field${fieldCount === 1 ? "" : "s"}`;
  }
  return Object.keys(schema).length ? "schema declared" : "—";
}

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return (
    <dl className="definition-list">
      {Object.entries(values).map(([label, value]) => (
        <div key={label}><dt>{label.replaceAll("_", " ")}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
