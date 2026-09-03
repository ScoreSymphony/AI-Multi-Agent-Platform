import { useCallback, useEffect, useState } from "react";
import { ControlPlaneClient } from "../api/client";
import type { CanonicalModel, CanonicalModelProvider, Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  Card,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const MODEL_QUERY_KEY = "models:display_name:asc";
const PROVIDER_QUERY_KEY = "model-providers:id:asc";

export function ModelsPage({ client }: { client: ControlPlaneClient }) {
  const [models, setModels] = useState<Page<CanonicalModel> | null>(null);
  const [providers, setProviders] = useState<Page<CanonicalModelProvider> | null>(null);
  const [modelError, setModelError] = useState<unknown>(null);
  const [providerError, setProviderError] = useState<unknown>(null);
  const modelPagination = useCursorPagination(MODEL_QUERY_KEY);
  const providerPagination = useCursorPagination(PROVIDER_QUERY_KEY);

  const loadModels = useCallback(async () => {
    try {
      setModels(
        await client.listModels({
          limit: 100,
          cursor: modelPagination.cursor,
          sort: "display_name",
          direction: "asc",
        }),
      );
      setModelError(null);
    } catch (nextError) {
      setModelError(nextError);
    }
  }, [client, modelPagination.cursor]);

  const loadProviders = useCallback(async () => {
    try {
      setProviders(
        await client.listModelProviders({
          limit: 100,
          cursor: providerPagination.cursor,
          sort: "id",
          direction: "asc",
        }),
      );
      setProviderError(null);
    } catch (nextError) {
      setProviderError(nextError);
    }
  }, [client, providerPagination.cursor]);

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

  if (!models && !providers && !modelError && !providerError) return <LoadingState />;

  const enabledOnPage = models?.items.filter((model) => model.enabled).length ?? "—";
  const healthyProvidersOnPage =
    providers?.items.filter(
      (provider) => provider.enabled && provider.available && provider.health === "healthy",
    ).length ?? "—";

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Model Registry</p>
        <h1>Models & providers</h1>
        <p>Canonical model configurations and provider instances from the platform-owned Control Plane.</p>
      </header>

      {modelError || providerError ? (
        <DegradedState
          title="Partial model inventory"
          detail={`Unavailable sections: ${[modelError ? "models" : null, providerError ? "providers" : null].filter(Boolean).join(", ")}. No provider-private fallback is used.`}
        />
      ) : null}

      <div className="metrics">
        <Metric label="Models" value={models?.total ?? "—"} />
        <Metric label="Enabled on page" value={enabledOnPage} />
        <Metric label="Providers" value={providers?.total ?? "—"} />
        <Metric label="Healthy on page" value={healthyProvidersOnPage} />
      </div>

      <Card title="Canonical models">
        {modelError ? <ErrorState error={modelError} onRetry={() => void loadModels()} /> : null}
        {models ? <ModelTable models={models.items} /> : modelError ? null : <LoadingState />}
        {models ? (
          <PaginationControls
            page={models}
            pageNumber={modelPagination.pageNumber}
            hasPrevious={modelPagination.hasPrevious}
            onPrevious={modelPagination.previous}
            onRefresh={() => void loadModels()}
            onNext={() => modelPagination.next(models.next_cursor)}
          />
        ) : null}
      </Card>

      <Card title="Provider instances">
        {providerError ? <ErrorState error={providerError} onRetry={() => void loadProviders()} /> : null}
        {providers ? <ProviderTable providers={providers.items} /> : providerError ? null : <LoadingState />}
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

function ModelTable({ models }: { models: CanonicalModel[] }) {
  if (!models.length) return <EmptyState title="No registered models" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Model</th><th>Health</th><th>Location</th><th>Provider</th><th>Capabilities</th></tr></thead>
        <tbody>{models.map((model) => (
          <tr key={model.id}>
            <td>
              <AppLink href={`/models/${encodeURIComponent(model.id)}`}>{model.display_name}</AppLink>
              <div><code>{model.id}</code></div>
              {!model.enabled ? <small>disabled</small> : null}
            </td>
            <td><StatusBadge value={model.effective_health} /></td>
            <td>{model.location}</td>
            <td><AppLink href={`/models/providers/${encodeURIComponent(model.provider_id)}`}>{model.provider_id}</AppLink></td>
            <td>{compactCapabilities(model)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function ProviderTable({ providers }: { providers: CanonicalModelProvider[] }) {
  if (!providers.length) return <EmptyState title="No registered model providers" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Provider</th><th>Type</th><th>Health</th><th>Enabled</th><th>Available</th></tr></thead>
        <tbody>{providers.map((provider) => (
          <tr key={provider.id}>
            <td><AppLink href={`/models/providers/${encodeURIComponent(provider.id)}`}>{provider.id}</AppLink></td>
            <td>{provider.provider_type}</td>
            <td><StatusBadge value={provider.health} /></td>
            <td>{provider.enabled ? "yes" : "no"}</td>
            <td>{provider.available ? "yes" : "no"}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function compactCapabilities(model: CanonicalModel): string {
  const values = [
    model.capabilities.tool_calling ? "tools" : null,
    model.capabilities.structured_output ? "structured" : null,
    model.capabilities.streaming ? "stream" : null,
    ...model.capabilities.modalities,
  ].filter((value): value is string => value !== null);
  return values.join(", ") || "—";
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}