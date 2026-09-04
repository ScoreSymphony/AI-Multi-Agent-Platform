import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  IntegrationsClient,
  type CanonicalConnection,
  type CanonicalConnectorDefinition,
  type CanonicalSecretReference,
  type ConnectorSyncMode,
  type ConnectorSyncResult,
} from "../api/integrations";
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

const DEFINITION_QUERY_KEY = "integrations:connector-definitions";
const CONNECTION_QUERY_KEY = "integrations:connections";

export function IntegrationsPage({ client }: { client: IntegrationsClient }) {
  const [definitions, setDefinitions] = useState<Page<CanonicalConnectorDefinition> | null>(null);
  const [connections, setConnections] = useState<Page<CanonicalConnection> | null>(null);
  const [definitionError, setDefinitionError] = useState<unknown>(null);
  const [connectionError, setConnectionError] = useState<unknown>(null);
  const [createError, setCreateError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [createdConnection, setCreatedConnection] = useState<CanonicalConnection | null>(null);
  const definitionPagination = useCursorPagination(DEFINITION_QUERY_KEY);
  const connectionPagination = useCursorPagination(CONNECTION_QUERY_KEY);

  const loadDefinitions = useCallback(async () => {
    try {
      setDefinitions(await client.listDefinitions({ limit: 50, cursor: definitionPagination.cursor }));
      setDefinitionError(null);
    } catch (error) {
      setDefinitionError(error);
    }
  }, [client, definitionPagination.cursor]);

  const loadConnections = useCallback(async () => {
    try {
      setConnections(await client.listConnections({ limit: 50, cursor: connectionPagination.cursor }));
      setConnectionError(null);
    } catch (error) {
      setConnectionError(error);
    }
  }, [client, connectionPagination.cursor]);

  useEffect(() => void loadDefinitions(), [loadDefinitions]);
  useEffect(() => void loadConnections(), [loadConnections]);

  const createConnection = async (input: CreateConnectionFormValue) => {
    setCreating(true);
    setCreateError(null);
    try {
      const created = await client.createConnection({
        connector_type_id: input.connectorTypeId,
        connector_version: input.connectorVersion,
        owner_type: input.ownerType,
        owner_id: input.ownerId,
        display_name: input.displayName,
        project_id: blankToUndefined(input.projectId),
        organization_id: blankToUndefined(input.organizationId),
        endpoint_metadata: parseJsonObject(input.endpointMetadata, "Endpoint metadata"),
        secret_references: parseSecretReferences(input.secretReferences),
        requested_scopes: splitCommaList(input.requestedScopes),
        approval_id: blankToUndefined(input.approvalId),
      });
      setCreatedConnection(created);
      await loadConnections();
    } catch (error) {
      setCreateError(error);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">External systems</p>
        <h1>Integrations</h1>
        <p>
          Canonical Connector Definitions and Connections. External provider actions are not invoked
          here; they remain behind the Capability and authorization pipeline.
        </p>
      </header>

      <Card title="Connector definitions">
        <p>Versioned capabilities and configuration contracts advertised by registered connector providers.</p>
        {definitionError ? <ErrorState error={definitionError} onRetry={() => void loadDefinitions()} /> : null}
        {!definitions ? <LoadingState label="Loading Connector Definitions…" /> : (
          <>
            <DefinitionTable definitions={definitions.items} />
            <PaginationControls
              page={definitions}
              pageNumber={definitionPagination.pageNumber}
              hasPrevious={definitionPagination.hasPrevious}
              onPrevious={definitionPagination.previous}
              onRefresh={() => void loadDefinitions()}
              onNext={() => definitionPagination.next(definitions.next_cursor)}
            />
          </>
        )}
      </Card>

      <Card title="Connections">
        <p>Configured canonical accounts/endpoints with safe metadata and secret references only.</p>
        {connectionError ? <ErrorState error={connectionError} onRetry={() => void loadConnections()} /> : null}
        {!connections ? <LoadingState label="Loading Connections…" /> : (
          <>
            <ConnectionTable connections={connections.items} />
            <PaginationControls
              page={connections}
              pageNumber={connectionPagination.pageNumber}
              hasPrevious={connectionPagination.hasPrevious}
              onPrevious={connectionPagination.previous}
              onRefresh={() => void loadConnections()}
              onNext={() => connectionPagination.next(connections.next_cursor)}
            />
          </>
        )}
      </Card>

      <Card title="Create connection">
        <p>
          Reference existing secrets by canonical SecretReference. Do not put credential material in
          endpoint metadata; the server rejects embedded credentials.
        </p>
        {createError ? <ErrorState error={createError} /> : null}
        {createdConnection ? (
          <p role="status">
            Created <AppLink href={`/integrations/connections/${encodeURIComponent(createdConnection.id)}`}>
              <CanonicalId value={createdConnection.id} />
            </AppLink>.
          </p>
        ) : null}
        <CreateConnectionForm disabled={creating} onSubmit={createConnection} />
      </Card>
    </div>
  );
}

export function ConnectorDefinitionDetailPage({
  client,
  definitionId,
}: {
  client: IntegrationsClient;
  definitionId: string;
}) {
  const [definition, setDefinition] = useState<CanonicalConnectorDefinition | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setDefinition(await client.getDefinition(definitionId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, definitionId]);

  useEffect(() => void load(), [load]);

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!definition) return <LoadingState label="Loading Connector Definition…" />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Integrations / Definition</p>
        <h1>{definition.name}</h1>
        <p><CanonicalId value={definition.id} /> · {definition.connector_type_id}@{definition.version}</p>
      </header>

      <Card title="Declared surface">
        <p>{definition.description || "No description."}</p>
        <dl className="detail-grid">
          <Detail label="Supported operations"><TextList values={definition.supported_operations} /></Detail>
          <Detail label="Features"><TextList values={definition.features} /></Detail>
          <Detail label="Authentication"><TextList values={definition.authentication_requirements} /></Detail>
          <Detail label="Resource types"><TextList values={definition.resource_types} /></Detail>
          <Detail label="Capability actions"><TextList values={definition.actions} /></Detail>
          <Detail label="Event types"><TextList values={definition.event_types} /></Detail>
        </dl>
      </Card>

      <Card title="Configuration contract">
        <JsonBlock value={definition.configuration_schema} />
      </Card>
      <Card title="Health semantics">
        <JsonBlock value={definition.health_semantics} />
      </Card>
      <Card title="Source metadata">
        <JsonBlock value={definition.source_metadata} />
      </Card>
    </div>
  );
}

export function ConnectionDetailPage({
  client,
  connectionId,
}: {
  client: IntegrationsClient;
  connectionId: string;
}) {
  const [connection, setConnection] = useState<CanonicalConnection | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [syncResult, setSyncResult] = useState<ConnectorSyncResult | null>(null);
  const [removed, setRemoved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [approvalId, setApprovalId] = useState("");
  const [syncStream, setSyncStream] = useState("");
  const [syncMode, setSyncMode] = useState<ConnectorSyncMode>("incremental");

  const load = useCallback(async () => {
    try {
      setConnection(await client.getConnection(connectionId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, connectionId]);

  useEffect(() => void load(), [load]);

  const mutate = async (action: () => Promise<CanonicalConnection>) => {
    setBusy(true);
    setActionError(null);
    try {
      setConnection(await action());
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!connection) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await client.removeConnection(
        connection.id,
        blankToUndefined(approvalId),
      );
      if (result.removed) setRemoved(true);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const synchronize = async (event: FormEvent) => {
    event.preventDefault();
    if (!connection) return;
    setBusy(true);
    setActionError(null);
    try {
      setSyncResult(await client.synchronize(connection.id, syncStream, syncMode));
      await load();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (removed) {
    return (
      <div className="stack">
        <header className="page-header"><p className="eyebrow">Integrations</p><h1>Connection removed</h1></header>
        <Card title="Historical identity preserved">
          <p>The Connection was removed from active configuration. Historical canonical references keep their existing identity.</p>
          <AppLink href="/integrations">Back to Integrations</AppLink>
        </Card>
      </div>
    );
  }
  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!connection) return <LoadingState label="Loading Connection…" />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Integrations / Connection</p>
        <h1>{connection.display_name}</h1>
        <p><CanonicalId value={connection.id} /></p>
      </header>
      {actionError ? <ErrorState error={actionError} /> : null}

      <Card title="Lifecycle">
        <dl className="detail-grid">
          <Detail label="Connector">{connection.connector_type_id}@{connection.connector_version}</Detail>
          <Detail label="Status"><StatusBadge value={connection.status} /></Detail>
          <Detail label="Health"><StatusBadge value={connection.health} /></Detail>
          <Detail label="Enabled">{connection.enabled ? "yes" : "no"}</Detail>
          <Detail label="Revision">{connection.revision}</Detail>
          <Detail label="Owner">{connection.owner_type} · <CanonicalId value={connection.owner_id} /></Detail>
          <Detail label="Project">{connection.project_id ? <CanonicalId value={connection.project_id} /> : "—"}</Detail>
          <Detail label="Organization">{connection.organization_id ? <CanonicalId value={connection.organization_id} /> : "—"}</Detail>
          <Detail label="Created">{formatTimestamp(connection.created_at)}</Detail>
          <Detail label="Updated">{formatTimestamp(connection.updated_at)}</Detail>
          <Detail label="Last health check">{connection.last_checked_at ? formatTimestamp(connection.last_checked_at) : "—"}</Detail>
        </dl>
        <label className="field">
          <span>Approval ID (optional for lifecycle mutations)</span>
          <input value={approvalId} onChange={(event) => setApprovalId(event.target.value)} />
        </label>
        <div className="button-row" aria-label="Connection lifecycle">
          <button disabled={busy} onClick={() => void mutate(() => connection.enabled
            ? client.disableConnection(connection.id, blankToUndefined(approvalId))
            : client.enableConnection(connection.id, blankToUndefined(approvalId)))}>
            {connection.enabled ? "Disable connection" : "Enable connection"}
          </button>
          <button disabled={busy} onClick={() => void mutate(() => client.checkConnectionHealth(connection.id))}>
            Check health
          </button>
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
          <button disabled={busy} onClick={() => void remove()}>Remove connection</button>
        </div>
      </Card>

      <Card title="Scopes and safe metadata">
        <dl className="detail-grid">
          <Detail label="Requested scopes"><TextList values={connection.requested_scopes} /></Detail>
          <Detail label="Granted scopes"><TextList values={connection.granted_scopes} /></Detail>
        </dl>
        <h3>Endpoint metadata</h3>
        <JsonBlock value={connection.endpoint_metadata} />
        <h3>Account metadata</h3>
        <JsonBlock value={connection.account_metadata} />
        <h3>Secret references</h3>
        {connection.secret_references.length === 0 ? <p>None.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Provider</th><th>Secret ID</th><th>Scope</th><th>Version</th></tr></thead>
              <tbody>{connection.secret_references.map((reference, index) => (
                <tr key={`${reference.provider}:${reference.secret_id}:${index}`}>
                  <td>{reference.provider}</td><td><code>{reference.secret_id}</code></td>
                  <td><code>{reference.scope}</code></td><td>{reference.version ?? "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        <p className="muted">Only references are rendered; secret material is not part of the Connection contract.</p>
      </Card>

      <Card title="Synchronization">
        <form className="form-grid" onSubmit={(event) => void synchronize(event)}>
          <label className="field"><span>Stream</span><input required value={syncStream} onChange={(event) => setSyncStream(event.target.value)} /></label>
          <label className="field"><span>Mode</span><select value={syncMode} onChange={(event) => setSyncMode(event.target.value as ConnectorSyncMode)}><option value="incremental">incremental</option><option value="resync">resync</option><option value="rebuild">rebuild</option></select></label>
          <button disabled={busy} type="submit">Synchronize</button>
        </form>
        {syncResult ? (
          <div className="stack" role="status">
            <dl className="detail-grid">
              <Detail label="Status"><StatusBadge value={syncResult.status} /></Detail>
              <Detail label="Mode">{syncResult.mode}</Detail>
              <Detail label="Stream">{syncResult.stream}</Detail>
              <Detail label="Cursor">{syncResult.cursor ?? "—"}</Detail>
              <Detail label="Last successful sync">{syncResult.last_successful_sync ? formatTimestamp(syncResult.last_successful_sync) : "—"}</Detail>
              <Detail label="Resource refs">{syncResult.resource_refs.length}</Detail>
              <Detail label="Events">{syncResult.events.length}</Detail>
            </dl>
            {syncResult.resource_refs.length > 0 ? <JsonBlock value={syncResult.resource_refs} /> : null}
          </div>
        ) : null}
      </Card>
    </div>
  );
}

interface CreateConnectionFormValue {
  connectorTypeId: string;
  connectorVersion: string;
  ownerType: string;
  ownerId: string;
  displayName: string;
  projectId: string;
  organizationId: string;
  endpointMetadata: string;
  secretReferences: string;
  requestedScopes: string;
  approvalId: string;
}

function CreateConnectionForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (value: CreateConnectionFormValue) => Promise<void>;
}) {
  const [value, setValue] = useState<CreateConnectionFormValue>({
    connectorTypeId: "",
    connectorVersion: "",
    ownerType: "user",
    ownerId: "",
    displayName: "",
    projectId: "",
    organizationId: "",
    endpointMetadata: "{}",
    secretReferences: "[]",
    requestedScopes: "",
    approvalId: "",
  });
  const set = (key: keyof CreateConnectionFormValue, next: string) => setValue((current) => ({ ...current, [key]: next }));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void onSubmit(value);
  };
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Connector type ID</span><input required value={value.connectorTypeId} onChange={(event) => set("connectorTypeId", event.target.value)} /></label>
      <label className="field"><span>Connector version</span><input required value={value.connectorVersion} onChange={(event) => set("connectorVersion", event.target.value)} /></label>
      <label className="field"><span>Display name</span><input required value={value.displayName} onChange={(event) => set("displayName", event.target.value)} /></label>
      <label className="field"><span>Owner type</span><input required value={value.ownerType} onChange={(event) => set("ownerType", event.target.value)} /></label>
      <label className="field"><span>Owner ID</span><input required value={value.ownerId} onChange={(event) => set("ownerId", event.target.value)} /></label>
      <label className="field"><span>Project ID (optional)</span><input value={value.projectId} onChange={(event) => set("projectId", event.target.value)} /></label>
      <label className="field"><span>Organization ID (optional)</span><input value={value.organizationId} onChange={(event) => set("organizationId", event.target.value)} /></label>
      <label className="field"><span>Requested scopes (comma-separated)</span><input value={value.requestedScopes} onChange={(event) => set("requestedScopes", event.target.value)} /></label>
      <label className="field"><span>Approval ID (optional)</span><input value={value.approvalId} onChange={(event) => set("approvalId", event.target.value)} /></label>
      <label className="field field-wide"><span>Endpoint metadata JSON</span><textarea rows={5} value={value.endpointMetadata} onChange={(event) => set("endpointMetadata", event.target.value)} /></label>
      <label className="field field-wide"><span>SecretReference array JSON</span><textarea rows={6} value={value.secretReferences} onChange={(event) => set("secretReferences", event.target.value)} /></label>
      <button disabled={disabled} type="submit">Create connection</button>
    </form>
  );
}

function DefinitionTable({ definitions }: { definitions: CanonicalConnectorDefinition[] }) {
  if (definitions.length === 0) return <EmptyState title="No Connector Definitions" detail="No connector providers are currently registered." />;
  return <div className="table-wrap"><table><thead><tr><th>Definition</th><th>Type / version</th><th>Operations</th><th>Resources</th></tr></thead><tbody>{definitions.map((definition) => <tr key={definition.id}><td><AppLink href={`/integrations/definitions/${encodeURIComponent(definition.id)}`}>{definition.name}</AppLink><br /><CanonicalId value={definition.id} /></td><td>{definition.connector_type_id}@{definition.version}</td><td>{definition.supported_operations.length}</td><td>{definition.resource_types.join(", ") || "—"}</td></tr>)}</tbody></table></div>;
}

function ConnectionTable({ connections }: { connections: CanonicalConnection[] }) {
  if (connections.length === 0) return <EmptyState title="No Connections" detail="No external account or endpoint is configured." />;
  return <div className="table-wrap"><table><thead><tr><th>Connection</th><th>Connector</th><th>Status</th><th>Health</th><th>Scope</th></tr></thead><tbody>{connections.map((connection) => <tr key={connection.id}><td><AppLink href={`/integrations/connections/${encodeURIComponent(connection.id)}`}>{connection.display_name}</AppLink><br /><CanonicalId value={connection.id} /></td><td>{connection.connector_type_id}@{connection.connector_version}</td><td><StatusBadge value={connection.status} />{connection.enabled ? "" : " · disabled"}</td><td><StatusBadge value={connection.health} /></td><td>{connection.project_id ? <CanonicalId value={connection.project_id} /> : connection.organization_id ? <CanonicalId value={connection.organization_id} /> : `${connection.owner_type}:${connection.owner_id}`}</td></tr>)}</tbody></table></div>;
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return <><dt>{label}</dt><dd>{children}</dd></>;
}

function TextList({ values }: { values: string[] }) {
  return values.length === 0 ? <>—</> : <>{values.join(", ")}</>;
}

function JsonBlock({ value }: { value: JsonValue | CanonicalConnectorDefinition["source_metadata"] | ConnectorSyncResult["resource_refs"] }) {
  return <pre className="code-block"><code>{JSON.stringify(value, null, 2)}</code></pre>;
}

function blankToUndefined(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function splitCommaList(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function parseJsonObject(value: string, label: string): Record<string, JsonValue> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value || "{}");
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return parsed as Record<string, JsonValue>;
}

function parseSecretReferences(value: string): CanonicalSecretReference[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value || "[]");
  } catch {
    throw new Error("Secret references must be valid JSON");
  }
  if (!Array.isArray(parsed)) throw new Error("Secret references must be a JSON array");
  return parsed.map((item, index) => {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new Error(`Secret reference ${index + 1} must be an object`);
    }
    const candidate = item as Record<string, unknown>;
    if (typeof candidate.provider !== "string" || typeof candidate.secret_id !== "string" || typeof candidate.scope !== "string") {
      throw new Error(`Secret reference ${index + 1} requires provider, secret_id and scope strings`);
    }
    if (candidate.version !== undefined && candidate.version !== null && typeof candidate.version !== "string") {
      throw new Error(`Secret reference ${index + 1} version must be a string or null`);
    }
    if (candidate.metadata !== undefined && (typeof candidate.metadata !== "object" || candidate.metadata === null || Array.isArray(candidate.metadata))) {
      throw new Error(`Secret reference ${index + 1} metadata must be an object`);
    }
    return {
      provider: candidate.provider,
      secret_id: candidate.secret_id,
      scope: candidate.scope,
      version: candidate.version as string | null | undefined,
      metadata: (candidate.metadata ?? {}) as Record<string, JsonValue>,
    };
  });
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
