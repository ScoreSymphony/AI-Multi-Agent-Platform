import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import {
  ApplicationsClient,
  type CanonicalApplication,
  type CanonicalApplicationConfigurationField,
  type CanonicalApplicationInstance,
  type CanonicalApplicationLogStream,
} from "../api/applications";
import type { JsonValue, Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  CanonicalId,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const APPLICATION_QUERY_KEY = "applications:definitions";
const INSTANCE_QUERY_KEY = "applications:instances";

export function ApplicationsPage({ client }: { client: ApplicationsClient }) {
  const [applications, setApplications] = useState<Page<CanonicalApplication> | null>(null);
  const [instances, setInstances] = useState<Page<CanonicalApplicationInstance> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const applicationPagination = useCursorPagination(APPLICATION_QUERY_KEY);
  const instancePagination = useCursorPagination(INSTANCE_QUERY_KEY);

  const load = useCallback(async () => {
    try {
      const [nextApplications, nextInstances] = await Promise.all([
        client.listApplications({ limit: 50, cursor: applicationPagination.cursor }),
        client.listInstances({ limit: 50, cursor: instancePagination.cursor }),
      ]);
      setApplications(nextApplications);
      setInstances(nextInstances);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [applicationPagination.cursor, client, instancePagination.cursor]);

  useEffect(() => void load(), [load]);

  const healthy = instances?.items.filter((item) => item.health === "healthy").length ?? "—";
  const running = instances?.items.filter((item) => item.observed_state === "running").length ?? "—";

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Managed application adapters</p>
        <h1>Applications</h1>
        <p>
          Inspect and operate declaratively managed applications through the canonical Control
          Plane. Lifecycle actions, health, endpoints and diagnostics remain runtime-neutral.
        </p>
      </header>

      <div className="metrics">
        <Metric label="Installed definitions" value={applications?.total ?? "—"} />
        <Metric label="Instances" value={instances?.total ?? "—"} />
        <Metric label="Running on page" value={running} />
        <Metric label="Healthy on page" value={healthy} />
      </div>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      <Card title="Application instances">
        <div className="actions"><button onClick={() => void load()}>Refresh</button></div>
        {!instances && !error ? <LoadingState label="Loading Application instances…" /> : null}
        {instances ? <ApplicationInstanceTable instances={instances.items} /> : null}
        {instances ? (
          <PaginationControls
            page={instances}
            pageNumber={instancePagination.pageNumber}
            hasPrevious={instancePagination.hasPrevious}
            onPrevious={instancePagination.previous}
            onRefresh={() => void load()}
            onNext={() => instancePagination.next(instances.next_cursor)}
          />
        ) : null}
      </Card>

      <Card title="Installed definitions">
        {!applications && !error ? <LoadingState label="Loading Applications…" /> : null}
        {applications ? <ApplicationDefinitionTable applications={applications.items} /> : null}
        {applications ? (
          <PaginationControls
            page={applications}
            pageNumber={applicationPagination.pageNumber}
            hasPrevious={applicationPagination.hasPrevious}
            onPrevious={applicationPagination.previous}
            onRefresh={() => void load()}
            onNext={() => applicationPagination.next(applications.next_cursor)}
          />
        ) : null}
      </Card>
    </div>
  );
}

export function ApplicationDetailPage({
  client,
  instanceId,
}: {
  client: ApplicationsClient;
  instanceId: string;
}) {
  const [instance, setInstance] = useState<CanonicalApplicationInstance | null>(null);
  const [application, setApplication] = useState<CanonicalApplication | null>(null);
  const [logs, setLogs] = useState<CanonicalApplicationLogStream | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [logsError, setLogsError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const nextInstance = await client.getInstance(instanceId);
      const nextApplication = await client.getApplication(nextInstance.application_ref);
      setInstance(nextInstance);
      setApplication(nextApplication);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, instanceId]);

  const loadLogs = useCallback(async () => {
    try {
      setLogs(await client.getLogs(instanceId));
      setLogsError(null);
    } catch (nextError) {
      setLogsError(nextError);
    }
  }, [client, instanceId]);

  useEffect(() => void load(), [load]);
  useEffect(() => void loadLogs(), [loadLogs]);

  const mutate = async (
    operation: (id: string) => Promise<CanonicalApplicationInstance>,
  ) => {
    setBusy(true);
    setActionError(null);
    try {
      setInstance(await operation(instanceId));
      await loadLogs();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!instance || !application) return <LoadingState label="Loading Application instance…" />;

  const actionState = applicationActionState(instance);

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Applications / Instance</p>
        <h1>{application.name}</h1>
        <p>
          <CanonicalId value={instance.id} /> · {application.version} · {instance.runtime_id}
        </p>
      </header>

      {actionError ? <ErrorState error={actionError} /> : null}

      <Card title="Lifecycle and health">
        <dl className="detail-grid">
          <Detail label="Desired"><StatusBadge value={instance.desired_state} /></Detail>
          <Detail label="Observed"><StatusBadge value={instance.observed_state} /></Detail>
          <Detail label="Health"><StatusBadge value={instance.health} /></Detail>
          <Detail label="Runtime">{instance.runtime_id}</Detail>
          <Detail label="Node">{instance.node_id ?? "local / unassigned"}</Detail>
          <Detail label="Revision">{instance.revision}</Detail>
          <Detail label="Updated">{new Date(instance.updated_at).toLocaleString()}</Detail>
          <Detail label="Source">{application.source_ref ?? "—"}</Detail>
        </dl>
        <div className="button-row" aria-label="Application lifecycle">
          <button disabled={busy || !actionState.start} onClick={() => void mutate((id) => client.start(id))}>Start</button>
          <button disabled={busy || !actionState.stop} onClick={() => void mutate((id) => client.stop(id))}>Stop</button>
          <button disabled={busy || !actionState.restart} onClick={() => void mutate((id) => client.restart(id))}>Restart</button>
          <button disabled={busy || !actionState.reconcile} onClick={() => void mutate((id) => client.reconcile(id))}>Reconcile</button>
          <button
            className="danger"
            disabled={busy || !actionState.remove}
            onClick={() => {
              if (window.confirm(`Remove Application instance ${instance.id}?`)) {
                void mutate((id) => client.remove(id));
              }
            }}
          >
            Remove
          </button>
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
          {instance.open ? <OpenApplication target={instance.open} /> : null}
        </div>
      </Card>

      <Card title="Services">
        {instance.service_states.length === 0 ? (
          <EmptyState title="No service observations" detail="The runtime has not reported service-level state yet." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Service</th><th>State</th><th>Health</th><th>Message</th></tr></thead>
              <tbody>
                {instance.service_states.map((service) => (
                  <tr key={service.service_id}>
                    <td><code>{service.service_id}</code></td>
                    <td><StatusBadge value={service.observed_state} /></td>
                    <td><StatusBadge value={service.health} /></td>
                    <td>{service.message ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Endpoints">
        {instance.endpoints.length === 0 ? (
          <EmptyState title="No resolved endpoints" detail="No runtime endpoint is currently advertised." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Endpoint</th><th>Exposure</th><th>URI</th></tr></thead>
              <tbody>
                {instance.endpoints.map((endpoint) => (
                  <tr key={endpoint.endpoint_ref}>
                    <td><code>{endpoint.endpoint_ref}</code></td>
                    <td>{endpoint.exposure}</td>
                    <td><code>{endpoint.uri}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Logs and diagnostics">
        <div className="actions"><button onClick={() => void loadLogs()}>Refresh logs</button></div>
        {logsError ? <ErrorState error={logsError} onRetry={() => void loadLogs()} /> : null}
        {!logs && !logsError ? <LoadingState label="Loading Application logs…" /> : null}
        {logs ? <ApplicationLogs stream={logs} /> : null}
      </Card>

      <Card title="Configuration and bindings">
        <ApplicationConfigurationForm
          application={application}
          instance={instance}
          busy={busy}
          onConfigure={(configuration) => void mutate((id) => client.configure(id, configuration))}
        />
        <dl className="detail-grid">
          <Detail label="Current configuration"><JsonBlock value={instance.configuration} /></Detail>
          <Detail label="Secret references"><JsonBlock value={instance.secret_bindings} /></Detail>
          <Detail label="Volume bindings"><JsonBlock value={instance.volume_bindings} /></Detail>
          <Detail label="Resource requirements"><JsonBlock value={application.manifest.resources} /></Detail>
        </dl>
        <p className="muted">
          Secret values are never returned to this surface; only canonical references are visible.
          Running instances converge configuration through the canonical lifecycle, including the
          owner-controlled restart semantics when required.
        </p>
      </Card>

      <Card title="Application definition">
        <p>{application.manifest.description}</p>
        <dl className="detail-grid">
          <Detail label="Application ID"><CanonicalId value={application.application_id} /></Detail>
          <Detail label="Version">{application.version}</Detail>
          <Detail label="Maturity">{application.manifest.maturity}</Detail>
          <Detail label="Runtime requirements">{application.manifest.runtime_requirements.join(", ") || "—"}</Detail>
        </dl>
        <JsonBlock value={application.manifest} />
        <p><AppLink href="/applications">Back to Applications</AppLink></p>
      </Card>
    </div>
  );
}

export function ApplicationConfigurationForm({
  application,
  instance,
  busy,
  onConfigure,
}: {
  application: CanonicalApplication;
  instance: CanonicalApplicationInstance;
  busy: boolean;
  onConfigure: (configuration: Record<string, JsonValue>) => void;
}) {
  const fields = application.manifest.configuration.filter((field) => field.mutable);
  if (fields.length === 0) {
    return (
      <EmptyState
        title="No mutable configuration"
        detail="This Application manifest exposes no user-editable configuration fields."
      />
    );
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const configuration: Record<string, JsonValue> = {};
    for (const field of fields) {
      configuration[field.name] = parseApplicationConfigurationValue(
        field,
        String(form.get(field.name) ?? ""),
      );
    }
    onConfigure(configuration);
  };

  return (
    <form className="form-grid" onSubmit={submit}>
      {fields.map((field) => (
        <ApplicationConfigurationInput
          key={field.name}
          field={field}
          value={instance.configuration[field.name] ?? field.default}
          disabled={busy || instance.desired_state === "removed" || instance.observed_state === "removed"}
        />
      ))}
      <button
        className="primary"
        type="submit"
        disabled={busy || instance.desired_state === "removed" || instance.observed_state === "removed"}
      >
        {busy ? "Saving…" : "Save configuration"}
      </button>
    </form>
  );
}

function ApplicationConfigurationInput({
  field,
  value,
  disabled,
}: {
  field: CanonicalApplicationConfigurationField;
  value: JsonValue;
  disabled: boolean;
}) {
  if (field.value_type === "boolean") {
    return (
      <label>
        {field.name}
        <select
          name={field.name}
          disabled={disabled}
          defaultValue={value === true ? "true" : "false"}
        >
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
        <small>{applicationConfigurationHint(field)}</small>
      </label>
    );
  }

  return (
    <label>
      {field.name}
      <input
        name={field.name}
        disabled={disabled}
        required={field.required}
        type={field.value_type === "string" ? "text" : "number"}
        step={field.value_type === "integer" ? "1" : field.value_type === "number" ? "any" : undefined}
        defaultValue={value === null ? "" : String(value)}
      />
      <small>{applicationConfigurationHint(field)}</small>
    </label>
  );
}

export function parseApplicationConfigurationValue(
  field: CanonicalApplicationConfigurationField,
  raw: string,
): JsonValue {
  if (!raw.trim()) {
    if (field.required) throw new Error(`Configuration field ${field.name} is required`);
    return null;
  }
  if (field.value_type === "string") return raw;
  if (field.value_type === "boolean") {
    if (raw === "true") return true;
    if (raw === "false") return false;
    throw new Error(`Configuration field ${field.name} must be true or false`);
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    throw new Error(`Configuration field ${field.name} must be a finite number`);
  }
  if (field.value_type === "integer" && !Number.isInteger(value)) {
    throw new Error(`Configuration field ${field.name} must be an integer`);
  }
  return value;
}

function applicationConfigurationHint(field: CanonicalApplicationConfigurationField): string {
  const parts = [field.value_type, field.required ? "required" : "optional"];
  if (field.environment_variable) parts.push(`runtime env ${field.environment_variable}`);
  return parts.join(" · ");
}

export function applicationActionState(instance: CanonicalApplicationInstance) {
  const removed = instance.desired_state === "removed" || instance.observed_state === "removed";
  return {
    start: !removed && instance.desired_state !== "running",
    stop: !removed && instance.desired_state !== "stopped",
    restart: !removed && instance.desired_state === "running",
    reconcile: !removed,
    remove: !removed && instance.desired_state === "stopped",
  };
}

function ApplicationInstanceTable({ instances }: { instances: CanonicalApplicationInstance[] }) {
  if (instances.length === 0) return <EmptyState title="No Application instances" detail="Installed Application instances will appear here." />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Instance</th><th>Application</th><th>State</th><th>Health</th><th>Runtime</th><th>Open</th></tr></thead>
        <tbody>
          {instances.map((instance) => (
            <tr key={instance.id}>
              <td><AppLink href={`/applications/${encodeURIComponent(instance.id)}`}><CanonicalId value={instance.id} /></AppLink></td>
              <td><code>{instance.application_ref}</code></td>
              <td><StatusBadge value={instance.observed_state} /></td>
              <td><StatusBadge value={instance.health} /></td>
              <td>{instance.runtime_id}</td>
              <td>{instance.open ? <OpenApplication target={instance.open} compact /> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ApplicationDefinitionTable({ applications }: { applications: CanonicalApplication[] }) {
  if (applications.length === 0) return <EmptyState title="No Applications installed" detail="Definitions installed through the canonical Application Adapter will appear here." />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Name</th><th>Version</th><th>Runtime</th><th>Maturity</th><th>Source</th></tr></thead>
        <tbody>
          {applications.map((application) => (
            <tr key={application.id}>
              <td>{application.name}</td>
              <td>{application.version}</td>
              <td>{application.runtime_id}</td>
              <td>{application.manifest.maturity}</td>
              <td>{application.source_ref ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OpenApplication({
  target,
  compact = false,
}: {
  target: NonNullable<CanonicalApplicationInstance["open"]>;
  compact?: boolean;
}) {
  const external = target.open_mode === "external";
  return (
    <a
      href={target.uri}
      target={external ? "_blank" : undefined}
      rel={external ? "noreferrer" : undefined}
      title={`${target.endpoint_ref} · ${target.open_mode}`}
    >
      {compact ? "Open" : `Open ${target.endpoint_ref}`}
    </a>
  );
}

function ApplicationLogs({ stream }: { stream: CanonicalApplicationLogStream }) {
  const entries = stream.entries ?? [];
  if (entries.length === 0) return <EmptyState title="No runtime logs" detail="No bounded canonical log entries are currently available." />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Time</th><th>Service</th><th>Level</th><th>Message</th></tr></thead>
        <tbody>
          {entries.map((entry, index) => (
            <tr key={`${entry.timestamp}-${entry.service_id ?? "application"}-${index}`}>
              <td>{new Date(entry.timestamp).toLocaleString()}</td>
              <td>{entry.service_id ?? "application"}</td>
              <td><StatusBadge value={entry.level} /></td>
              <td><code>{entry.message}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>;
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
