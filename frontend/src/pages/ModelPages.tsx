import { useCallback, useEffect, useState } from "react";
import { ControlPlaneClient, prettyJson } from "../api/client";
import type {
  CanonicalModel,
  CanonicalModelProvider,
  JsonValue,
  Page,
} from "../api/types";
import { AppLink } from "../app/router";
import {
  Card,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";
import { usePermissionHint } from "../security/permissions";

export function ModelsPage({ client }: { client: ControlPlaneClient }) {
  const [models, setModels] = useState<Page<CanonicalModel> | null>(null);
  const [providers, setProviders] = useState<Page<CanonicalModelProvider> | null>(null);
  const [failures, setFailures] = useState<string[]>([]);

  const load = useCallback(async () => {
    const [modelResult, providerResult] = await Promise.allSettled([
      client.listModels({ limit: 100, sort: "display_name", direction: "asc" }),
      client.listModelProviders({ limit: 100, sort: "id", direction: "asc" }),
    ]);
    const nextFailures: string[] = [];
    if (modelResult.status === "fulfilled") setModels(modelResult.value);
    else nextFailures.push("models");
    if (providerResult.status === "fulfilled") setProviders(providerResult.value);
    else nextFailures.push("providers");
    setFailures(nextFailures);
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!models && !providers && failures.length === 0) return <LoadingState />;

  const enabledModels = models?.items.filter((model) => model.enabled).length ?? 0;
  const healthyProviders =
    providers?.items.filter(
      (provider) => provider.enabled && provider.available && provider.health === "healthy",
    ).length ?? 0;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Model Registry</p>
        <h1>Models & providers</h1>
        <p>
          Canonical model configurations and provider instances from the platform-owned Control
          Plane.
        </p>
      </header>
      {failures.length > 0 && (
        <DegradedState
          title="Partial model inventory"
          detail={`Unavailable sections: ${failures.join(", ")}. No provider-private fallback is used.`}
        />
      )}
      <div className="metrics">
        <Metric label="Models" value={models?.total ?? "—"} />
        <Metric label="Enabled models" value={models ? enabledModels : "—"} />
        <Metric label="Providers" value={providers?.total ?? "—"} />
        <Metric label="Healthy providers" value={providers ? healthyProviders : "—"} />
      </div>
      <Card title="Canonical models">
        {models ? <ModelTable models={models.items} /> : <EmptyState title="Models unavailable" />}
      </Card>
      <Card title="Provider instances">
        {providers ? (
          <ProviderTable providers={providers.items} />
        ) : (
          <EmptyState title="Providers unavailable" />
        )}
      </Card>
      <div className="actions">
        <button onClick={() => void load()}>Refresh inventory</button>
      </div>
    </div>
  );
}

export function ModelDetailPage({
  client,
  modelId,
}: {
  client: ControlPlaneClient;
  modelId: string;
}) {
  const [model, setModel] = useState<CanonicalModel | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const permission = usePermissionHint("model:command", modelId);

  const load = useCallback(async () => {
    try {
      setModel(await client.getModel(modelId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, modelId]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async () => {
    if (!model) return;
    setBusy(true);
    try {
      setModel(model.enabled ? await client.disableModel(model.id) : await client.enableModel(model.id));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error && !model) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!model) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Canonical model configuration</p>
          <h1>{model.display_name}</h1>
          <code title={model.id}>{model.id}</code>
        </div>
        <div className="detail-status">
          <StatusBadge value={model.effective_health} />
          <StatusBadge value={model.enabled ? "enabled" : "disabled"} />
        </div>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {permission === "denied" ? (
        <DegradedState
          title="Permission hint"
          detail="The current client hint marks model commands as denied. The Control Plane remains authoritative."
        />
      ) : null}
      <div className="actions">
        <button className={model.enabled ? undefined : "primary"} disabled={busy} onClick={() => void toggle()}>
          {model.enabled ? "Disable model" : "Enable model"}
        </button>
        <button disabled={busy} onClick={() => void load()}>Refresh</button>
      </div>
      <div className="grid-two">
        <Card title="Registry configuration">
          <DefinitionList
            values={{
              "Config ID": model.config_id,
              Provider: model.provider_id,
              Revision: model.revision,
              Location: model.location,
              Priority: model.priority,
              "Registry health": model.health,
              "Effective health": model.effective_health,
              "Node reference": model.node_ref ?? "—",
            }}
          />
        </Card>
        <Card title="Capabilities">
          <CapabilitySummary model={model} />
        </Card>
      </div>
      <Card title="Aliases">
        {model.aliases.length ? (
          <ul className="plain-list">{model.aliases.map((alias) => <li key={alias}><code>{alias}</code></li>)}</ul>
        ) : (
          <EmptyState title="No aliases" />
        )}
      </Card>
      <div className="grid-two">
        <Card title="Resource hints">
          <SafeJson value={model.resource_hints} />
        </Card>
        <Card title="Cost metadata">
          <SafeJson value={model.cost_metadata} />
        </Card>
      </div>
      <AdapterPanel title="Adapter-scoped model metadata" values={model.adapter_metadata} />
    </div>
  );
}

export function ModelProviderDetailPage({
  client,
  providerId,
}: {
  client: ControlPlaneClient;
  providerId: string;
}) {
  const [provider, setProvider] = useState<CanonicalModelProvider | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const permission = usePermissionHint("model-provider:command", providerId);

  const load = useCallback(async () => {
    try {
      setProvider(await client.getModelProvider(providerId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, providerId]);

  useEffect(() => {
    void load();
  }, [load]);

  const command = async (action: "enable" | "disable" | "refresh-health") => {
    if (!provider) return;
    setBusy(true);
    try {
      if (action === "enable") setProvider(await client.enableModelProvider(provider.id));
      if (action === "disable") setProvider(await client.disableModelProvider(provider.id));
      if (action === "refresh-health") {
        setProvider(await client.refreshModelProviderHealth(provider.id));
      }
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error && !provider) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!provider) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Model provider</p>
          <h1>{provider.id}</h1>
          <p>{provider.provider_type}</p>
        </div>
        <div className="detail-status">
          <StatusBadge value={provider.health} />
          <StatusBadge value={provider.enabled ? "enabled" : "disabled"} />
          <StatusBadge value={provider.available ? "available" : "unavailable"} />
        </div>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {permission === "denied" ? (
        <DegradedState
          title="Permission hint"
          detail="The current client hint marks provider commands as denied. The Control Plane remains authoritative."
        />
      ) : null}
      <div className="actions">
        {provider.enabled ? (
          <button disabled={busy} onClick={() => void command("disable")}>Disable provider</button>
        ) : (
          <button className="primary" disabled={busy} onClick={() => void command("enable")}>Enable provider</button>
        )}
        <button disabled={busy} onClick={() => void command("refresh-health")}>Refresh health</button>
        <button disabled={busy} onClick={() => void load()}>Refresh details</button>
      </div>
      <div className="grid-two">
        <Card title="Provider contract">
          <DefinitionList
            values={{
              ID: provider.id,
              Type: provider.provider_type,
              "Contract version": provider.contract_version,
              Health: provider.health,
              Enabled: provider.enabled ? "yes" : "no",
              Available: provider.available ? "yes" : "no",
            }}
          />
        </Card>
        <Card title="Supported operations">
          {provider.supported_operations.length ? (
            <ul className="plain-list">
              {provider.supported_operations.map((operation) => <li key={operation}><code>{operation}</code></li>)}
            </ul>
          ) : (
            <EmptyState title="No operations declared" />
          )}
        </Card>
      </div>
      <div className="grid-two">
        <Card title="Limits"><SafeJson value={provider.limits} /></Card>
        <Card title="Resources"><SafeJson value={provider.resources} /></Card>
      </div>
      <Card title="Declared capabilities">
        {provider.capabilities.length ? (
          <pre>{prettyJson(redactForDisplay(provider.capabilities))}</pre>
        ) : (
          <EmptyState title="No provider capabilities declared" />
        )}
      </Card>
      <AdapterPanel title="Adapter-scoped provider metadata" values={provider.adapter_metadata} />
    </div>
  );
}

function ModelTable({ models }: { models: CanonicalModel[] }) {
  if (!models.length) return <EmptyState title="No registered models" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Model</th><th>Health</th><th>Location</th><th>Provider</th><th>Capabilities</th></tr></thead>
        <tbody>
          {models.map((model) => (
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
          ))}
        </tbody>
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
        <tbody>
          {providers.map((provider) => (
            <tr key={provider.id}>
              <td><AppLink href={`/models/providers/${encodeURIComponent(provider.id)}`}>{provider.id}</AppLink></td>
              <td>{provider.provider_type}</td>
              <td><StatusBadge value={provider.health} /></td>
              <td>{provider.enabled ? "yes" : "no"}</td>
              <td>{provider.available ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CapabilitySummary({ model }: { model: CanonicalModel }) {
  const values = [
    model.capabilities.tool_calling ? "tool calling" : null,
    model.capabilities.structured_output ? "structured output" : null,
    model.capabilities.streaming ? "streaming" : null,
    ...model.capabilities.modalities.map((value) => `modality:${value}`),
    ...model.capabilities.reasoning.map((value) => `reasoning:${value}`),
  ].filter((value): value is string => value !== null);
  return (
    <div className="stack-tight">
      <DefinitionList values={{ "Context window": model.capabilities.context_window ?? "unknown" }} />
      {values.length ? <ul className="plain-list">{values.map((value) => <li key={value}>{value}</li>)}</ul> : <EmptyState title="No optional capabilities declared" />}
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

function AdapterPanel({ title, values }: { title: string; values: Array<Record<string, JsonValue>> }) {
  return (
    <Card title={title}>
      {values.length ? (
        <details>
          <summary>Show adapter metadata</summary>
          <pre>{prettyJson(redactForDisplay(values))}</pre>
        </details>
      ) : (
        <EmptyState title="No adapter metadata exposed" />
      )}
    </Card>
  );
}

function SafeJson({ value }: { value: JsonValue }) {
  const redacted = redactForDisplay(value);
  const empty = typeof redacted === "object" && redacted !== null && !Array.isArray(redacted) && Object.keys(redacted).length === 0;
  return empty ? <EmptyState title="No metadata" /> : <pre>{prettyJson(redacted)}</pre>;
}

function redactForDisplay(value: JsonValue): JsonValue {
  if (Array.isArray(value)) return value.map(redactForDisplay);
  if (typeof value !== "object" || value === null) return value;
  const sensitive = /(secret|password|token|api[_-]?key|authorization|cookie|credential)/i;
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, sensitive.test(key) ? "[REDACTED]" : redactForDisplay(item)]),
  );
}

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return <dl>{Object.entries(values).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
