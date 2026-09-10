import { useCallback, useEffect, useState } from "react";
import { ControlPlaneClient } from "../api/client";
import type { CanonicalModelRoutingProfile } from "../api/configuration";
import { useConfigurationSession } from "../api/configurationSession";
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
import { RoutingProfileConfigurationPage } from "./SingleNodeConfigurationPage";

const MODEL_QUERY_KEY = "models:display_name:asc";
const PROVIDER_QUERY_KEY = "model-providers:id:asc";

export function ModelsPage({ client }: { client: ControlPlaneClient }) {
  const [models, setModels] = useState<Page<CanonicalModel> | null>(null);
  const [providers, setProviders] = useState<Page<CanonicalModelProvider> | null>(null);
  const [routingProfiles, setRoutingProfiles] = useState<CanonicalModelRoutingProfile[] | null>(null);
  const [modelError, setModelError] = useState<unknown>(null);
  const [providerError, setProviderError] = useState<unknown>(null);
  const [routingError, setRoutingError] = useState<unknown>(null);
  const [creatingRoutingProfile, setCreatingRoutingProfile] = useState(false);
  const modelPagination = useCursorPagination(MODEL_QUERY_KEY);
  const providerPagination = useCursorPagination(PROVIDER_QUERY_KEY);
  const { configuration } = useConfigurationSession(client);

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

  const loadRoutingProfiles = useCallback(async () => {
    try {
      setRoutingProfiles((await configuration.listRoutingProfiles({ limit: 100 })).items);
      setRoutingError(null);
    } catch (nextError) {
      setRoutingError(nextError);
    }
  }, [configuration]);

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

  useEffect(() => {
    void loadRoutingProfiles();
  }, [loadRoutingProfiles]);

  if (creatingRoutingProfile) {
    return (
      <div className="stack">
        <div className="actions"><button type="button" onClick={() => setCreatingRoutingProfile(false)}>Back to Models</button></div>
        <RoutingProfileConfigurationPage core={client} configuration={configuration} />
      </div>
    );
  }

  if (!models && !providers && !modelError && !providerError) return <LoadingState />;

  const enabledOnPage = models?.items.filter((model) => model.enabled).length ?? "—";
  const healthyProvidersOnPage =
    providers?.items.filter(
      (provider) => provider.enabled && provider.available && provider.health === "healthy",
    ).length ?? "—";

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Model Registry</p>
          <h1>Models & providers</h1>
          <p>Canonical model configurations, provider instances and reusable routing policy for this platform.</p>
        </div>
        <button className="primary" type="button" onClick={() => setCreatingRoutingProfile(true)}>Create routing profile</button>
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

      <Card title="Model Routing Profiles">
        <p>Provider-neutral reusable model selection policies. Editing creates immutable canonical revisions.</p>
        {routingError ? <ErrorState error={routingError} onRetry={() => void loadRoutingProfiles()} /> : null}
        {routingProfiles === null && !routingError ? <LoadingState /> : null}
        {routingProfiles?.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Profile</th><th>Revision</th><th>Status</th><th>Project</th><th>Fallback</th></tr></thead>
              <tbody>{routingProfiles.map((profile) => (
                <tr key={profile.id}>
                  <td><AppLink href={`/model-routing-profiles/${encodeURIComponent(profile.id)}`}>{profile.revision.name}</AppLink><div><code>{profile.exact_ref}</code></div></td>
                  <td>{profile.current_revision}</td>
                  <td><StatusBadge value={profile.enabled ? "enabled" : "disabled"} /></td>
                  <td>{profile.project_id ?? "—"}</td>
                  <td>{profile.revision.policy.fallback}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : routingProfiles ? <EmptyState title="No Model Routing Profiles" /> : null}
      </Card>

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
