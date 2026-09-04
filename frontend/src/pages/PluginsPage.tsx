import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import {
  PluginsClient,
  type CanonicalPlugin,
  type CanonicalPluginCandidate,
  type PluginUpdateValidation,
} from "../api/plugins";
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

const PLUGIN_QUERY_KEY = "plugins:installed";
const CANDIDATE_QUERY_KEY = "plugins:candidates";

export function PluginsPage({
  client,
  candidateAvailable,
}: {
  client: PluginsClient;
  candidateAvailable: boolean;
}) {
  const [plugins, setPlugins] = useState<Page<CanonicalPlugin> | null>(null);
  const [candidates, setCandidates] = useState<Page<CanonicalPluginCandidate> | null>(null);
  const [pluginError, setPluginError] = useState<unknown>(null);
  const [candidateError, setCandidateError] = useState<unknown>(null);
  const pluginPagination = useCursorPagination(PLUGIN_QUERY_KEY);
  const candidatePagination = useCursorPagination(CANDIDATE_QUERY_KEY);

  const loadPlugins = useCallback(async () => {
    try {
      setPlugins(await client.listPlugins({ limit: 50, cursor: pluginPagination.cursor }));
      setPluginError(null);
    } catch (error) {
      setPluginError(error);
    }
  }, [client, pluginPagination.cursor]);

  const loadCandidates = useCallback(async () => {
    if (!candidateAvailable) {
      setCandidates(null);
      setCandidateError(null);
      return;
    }
    try {
      setCandidates(await client.listCandidates({ limit: 50, cursor: candidatePagination.cursor }));
      setCandidateError(null);
    } catch (error) {
      setCandidateError(error);
    }
  }, [candidateAvailable, client, candidatePagination.cursor]);

  useEffect(() => void loadPlugins(), [loadPlugins]);
  useEffect(() => void loadCandidates(), [loadCandidates]);

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Runtime extensions</p>
        <h1>Plugins</h1>
        <p>
          Canonical plugin lifecycle over the Control Plane. Plugins extend documented platform
          contracts; this surface does not load plugin-private modules or bypass authorization.
        </p>
      </header>

      <Card title="Installed plugins">
        <p>Installed state, compatibility, health, permissions, extensions and provenance.</p>
        {pluginError ? <ErrorState error={pluginError} onRetry={() => void loadPlugins()} /> : null}
        {!plugins ? <LoadingState label="Loading Plugins…" /> : (
          <>
            <PluginTable plugins={plugins.items} />
            <PaginationControls
              page={plugins}
              pageNumber={pluginPagination.pageNumber}
              hasPrevious={pluginPagination.hasPrevious}
              onPrevious={pluginPagination.previous}
              onRefresh={() => void loadPlugins()}
              onNext={() => pluginPagination.next(plugins.next_cursor)}
            />
          </>
        )}
      </Card>

      <Card title="Discovery candidates">
        {!candidateAvailable ? (
          <div className="degraded-state" role="status">
            <strong>Plugin discovery unavailable</strong>
            <p>
              The Control Plane advertises the installed Plugin registry but not
              <code> plugin-candidates</code>. Existing Plugins remain inspectable and manageable;
              install/update discovery is not inferred from filesystem or package state.
            </p>
          </div>
        ) : (
          <>
            <p>
              Inspect the exact discovered manifest and digest before installation or update
              validation. A changed digest is rejected server-side.
            </p>
            {candidateError ? <ErrorState error={candidateError} onRetry={() => void loadCandidates()} /> : null}
            {!candidates ? <LoadingState label="Loading Plugin Candidates…" /> : (
              <>
                <CandidateTable candidates={candidates.items} />
                <PaginationControls
                  page={candidates}
                  pageNumber={candidatePagination.pageNumber}
                  hasPrevious={candidatePagination.hasPrevious}
                  onPrevious={candidatePagination.previous}
                  onRefresh={() => void loadCandidates()}
                  onNext={() => candidatePagination.next(candidates.next_cursor)}
                />
              </>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

export function PluginDetailPage({
  client,
  pluginId,
  candidateAvailable,
}: {
  client: PluginsClient;
  pluginId: string;
  candidateAvailable: boolean;
}) {
  const [plugin, setPlugin] = useState<CanonicalPlugin | null>(null);
  const [candidate, setCandidate] = useState<CanonicalPluginCandidate | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [updateValidation, setUpdateValidation] = useState<PluginUpdateValidation | null>(null);
  const [configuration, setConfiguration] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [removed, setRemoved] = useState(false);

  const load = useCallback(async () => {
    try {
      setPlugin(await client.getPlugin(pluginId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, pluginId]);

  const loadCandidate = useCallback(async () => {
    if (!candidateAvailable) {
      setCandidate(null);
      return;
    }
    try {
      setCandidate(await client.getCandidate(pluginId));
    } catch {
      setCandidate(null);
    }
  }, [candidateAvailable, client, pluginId]);

  useEffect(() => void load(), [load]);
  useEffect(() => void loadCandidate(), [loadCandidate]);

  const mutate = async (action: () => Promise<CanonicalPlugin>) => {
    setBusy(true);
    setActionError(null);
    try {
      setPlugin(await action());
      await loadCandidate();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const configure = async (event: FormEvent) => {
    event.preventDefault();
    if (!plugin) return;
    setBusy(true);
    setActionError(null);
    try {
      const parsed = parseJsonObject(configuration, "Plugin configuration");
      setPlugin(await client.configure(plugin.id, parsed));
      setConfiguration("{}");
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const validateUpdate = async () => {
    if (!plugin || !candidate) return;
    setBusy(true);
    setActionError(null);
    setUpdateValidation(null);
    try {
      setUpdateValidation(await client.validateUpdate(plugin.id, candidate.manifest_digest));
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!plugin) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await client.remove(plugin.id);
      if (result.removed) setRemoved(true);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (removed) {
    return (
      <div className="stack">
        <header className="page-header"><p className="eyebrow">Plugins</p><h1>Plugin removed</h1></header>
        <Card title="Canonical history preserved">
          <p>
            The active Plugin registration was removed. Existing canonical Task, Run, Agent,
            Capability and historical extension references are not redefined by uninstalling it.
          </p>
          <AppLink href="/plugins">Back to Plugins</AppLink>
        </Card>
      </div>
    );
  }
  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!plugin) return <LoadingState label="Loading Plugin…" />;

  const canEnable = candidateAvailable && candidate !== null;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Plugins / Installed</p>
        <h1>{plugin.name}</h1>
        <p><CanonicalId value={plugin.id} /> · {plugin.plugin_version}</p>
      </header>
      {actionError ? <ErrorState error={actionError} /> : null}

      <Card title="Lifecycle and health">
        <dl className="detail-grid">
          <Detail label="State"><StatusBadge value={plugin.state} /></Detail>
          <Detail label="Compatibility"><StatusBadge value={plugin.compatibility} /></Detail>
          <Detail label="Health"><StatusBadge value={plugin.health} /></Detail>
          <Detail label="Health detail">{plugin.health_detail ?? "—"}</Detail>
          <Detail label="Configured">{plugin.configured ? "yes" : "no"}</Detail>
          <Detail label="Manifest version">{plugin.manifest_version}</Detail>
          <Detail label="Configuration version">{plugin.configuration_version}</Detail>
          <Detail label="State version">{plugin.state_version}</Detail>
          <Detail label="Install source">{plugin.install_source}</Detail>
        </dl>
        <div className="button-row" aria-label="Plugin lifecycle">
          {plugin.state === "enabled" ? (
            <button disabled={busy} onClick={() => void mutate(() => client.disable(plugin.id))}>
              Disable Plugin
            </button>
          ) : (
            <button
              disabled={busy || !canEnable}
              onClick={() => void mutate(() => client.enable(plugin.id, plugin.manifest_digest))}
              title={!canEnable ? "A matching discovery candidate is required before activation" : undefined}
            >
              Enable Plugin
            </button>
          )}
          <button disabled={busy} onClick={() => void mutate(() => client.refreshHealth(plugin.id))}>
            Refresh health
          </button>
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
          <button disabled={busy || plugin.state === "enabled"} onClick={() => void remove()}>
            Remove Plugin
          </button>
        </div>
        {!candidateAvailable ? (
          <p className="muted">
            Enable/update discovery is unavailable because the Control Plane does not advertise
            <code> plugin-candidates</code>. Existing lifecycle reads and safe actions remain usable.
          </p>
        ) : candidate === null ? (
          <p className="muted">
            No matching discovered candidate is currently available. The server will not activate a
            Plugin against an uninspected or mismatched manifest.
          </p>
        ) : null}
      </Card>

      <Card title="Permissions and extensions">
        <dl className="detail-grid">
          <Detail label="Requested permissions"><TextList values={plugin.requested_permissions} /></Detail>
          <Detail label="Granted permissions"><TextList values={plugin.granted_permissions} /></Detail>
          <Detail label="Capabilities"><TextList values={plugin.capabilities} /></Detail>
          <Detail label="Extension IDs"><TextList values={plugin.extension_ids} /></Detail>
          <Detail label="Extension types"><TextList values={plugin.extension_types} /></Detail>
          <Detail label="Dependencies"><TextList values={plugin.dependencies} /></Detail>
        </dl>
        <p className="muted">
          Requested permissions are declarations only. Granted permissions come from the
          authoritative server resolver when the Plugin is enabled.
        </p>
      </Card>

      <Card title="Configure">
        <p>
          Configuration is write-only from this browser surface: the Control Plane does not echo
          stored configuration values after `plugin.configure`, so the UI does not invent a readable
          configuration store.
        </p>
        <form className="form-grid" onSubmit={(event) => void configure(event)}>
          <label className="field field-wide">
            <span>Configuration JSON</span>
            <textarea rows={8} value={configuration} onChange={(event) => setConfiguration(event.target.value)} />
          </label>
          <button disabled={busy} type="submit">Apply configuration</button>
        </form>
        <h3>Configuration schema</h3>
        <JsonBlock value={plugin.manifest.configuration_schema} />
      </Card>

      <Card title="Manifest and provenance">
        <dl className="detail-grid">
          <Detail label="Author">{plugin.author || "—"}</Detail>
          <Detail label="Source">{plugin.provenance_source || "—"}</Detail>
          <Detail label="License">{plugin.provenance_license || "—"}</Detail>
          <Detail label="Manifest digest"><code>{plugin.manifest_digest}</code></Detail>
        </dl>
        <JsonBlock value={plugin.manifest as unknown as JsonValue} />
      </Card>

      <Card title="Discovered update">
        {!candidateAvailable ? (
          <p>Discovery catalog is not composed northbound.</p>
        ) : !candidate ? (
          <p>No candidate with this Plugin ID is currently advertised.</p>
        ) : (
          <div className="stack">
            <dl className="detail-grid">
              <Detail label="Candidate version">{candidate.plugin_version}</Detail>
              <Detail label="Candidate digest"><code>{candidate.manifest_digest}</code></Detail>
              <Detail label="Install source">{candidate.install_source}</Detail>
            </dl>
            <div className="button-row">
              <AppLink href={`/plugins/candidates/${encodeURIComponent(candidate.id)}`}>Inspect candidate</AppLink>
              <button disabled={busy} onClick={() => void validateUpdate()}>Validate update compatibility</button>
            </div>
            {updateValidation ? (
              <div role="status">
                <p><strong>{updateValidation.compatible ? "Compatible" : "Not compatible"}</strong></p>
                <p>{updateValidation.current_version} → {updateValidation.candidate_version}</p>
                <p><code>{updateValidation.manifest_digest}</code></p>
              </div>
            ) : null}
          </div>
        )}
      </Card>
    </div>
  );
}

export function PluginCandidateDetailPage({
  client,
  pluginId,
}: {
  client: PluginsClient;
  pluginId: string;
}) {
  const [candidate, setCandidate] = useState<CanonicalPluginCandidate | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [installed, setInstalled] = useState<CanonicalPlugin | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setCandidate(await client.getCandidate(pluginId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, pluginId]);

  useEffect(() => void load(), [load]);

  const install = async () => {
    if (!candidate) return;
    setBusy(true);
    setActionError(null);
    try {
      setInstalled(await client.install(candidate.id, candidate.manifest_digest));
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!candidate) return <LoadingState label="Loading Plugin Candidate…" />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Plugins / Candidate</p>
        <h1>{candidate.name}</h1>
        <p><CanonicalId value={candidate.id} /> · {candidate.plugin_version}</p>
      </header>
      {actionError ? <ErrorState error={actionError} /> : null}
      {installed ? (
        <Card title="Installed">
          <p role="status">
            The exact inspected manifest was installed as <AppLink href={`/plugins/${encodeURIComponent(installed.id)}`}>
              <CanonicalId value={installed.id} />
            </AppLink>. Configuration and activation remain separate lifecycle steps.
          </p>
        </Card>
      ) : null}

      <Card title="Inspection">
        <p>{candidate.description || "No description."}</p>
        <dl className="detail-grid">
          <Detail label="Author">{candidate.author || "—"}</Detail>
          <Detail label="Install source">{candidate.install_source}</Detail>
          <Detail label="Manifest version">{candidate.manifest_version}</Detail>
          <Detail label="Capabilities"><TextList values={candidate.capabilities} /></Detail>
          <Detail label="Requested permissions"><TextList values={candidate.requested_permissions} /></Detail>
          <Detail label="Extension IDs"><TextList values={candidate.extension_ids} /></Detail>
          <Detail label="Extension types"><TextList values={candidate.extension_types} /></Detail>
          <Detail label="Manifest digest"><code>{candidate.manifest_digest}</code></Detail>
        </dl>
        <button disabled={busy || installed !== null} onClick={() => void install()}>
          Install inspected manifest
        </button>
        <p className="muted">
          Installation submits this exact digest. If discovery changes before the command reaches the
          server, the Control Plane rejects the stale inspection instead of silently installing the
          new manifest.
        </p>
      </Card>

      <Card title="Full manifest">
        <JsonBlock value={candidate.manifest as unknown as JsonValue} />
      </Card>
    </div>
  );
}

function PluginTable({ plugins }: { plugins: CanonicalPlugin[] }) {
  if (plugins.length === 0) {
    return <EmptyState title="No installed Plugins" detail="No runtime extensions are currently installed." />;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Plugin</th><th>State</th><th>Compatibility</th><th>Health</th><th>Permissions</th></tr></thead>
        <tbody>{plugins.map((plugin) => (
          <tr key={plugin.id}>
            <td><AppLink href={`/plugins/${encodeURIComponent(plugin.id)}`}>{plugin.name}</AppLink><br /><CanonicalId value={plugin.id} /></td>
            <td><StatusBadge value={plugin.state} /></td>
            <td><StatusBadge value={plugin.compatibility} /></td>
            <td><StatusBadge value={plugin.health} /></td>
            <td>{plugin.granted_permissions.length} / {plugin.requested_permissions.length} granted</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function CandidateTable({ candidates }: { candidates: CanonicalPluginCandidate[] }) {
  if (candidates.length === 0) {
    return <EmptyState title="No Plugin Candidates" detail="The configured discovery sources currently advertise no Plugins." />;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Candidate</th><th>Version</th><th>Source</th><th>Permissions</th><th>Digest</th></tr></thead>
        <tbody>{candidates.map((candidate) => (
          <tr key={`${candidate.id}:${candidate.manifest_digest}`}>
            <td><AppLink href={`/plugins/candidates/${encodeURIComponent(candidate.id)}`}>{candidate.name}</AppLink><br /><CanonicalId value={candidate.id} /></td>
            <td>{candidate.plugin_version}</td>
            <td>{candidate.install_source}</td>
            <td>{candidate.requested_permissions.length}</td>
            <td><code>{shortDigest(candidate.manifest_digest)}</code></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return <><dt>{label}</dt><dd>{children}</dd></>;
}

function TextList({ values }: { values: string[] }) {
  return values.length === 0 ? <>—</> : <>{values.join(", ")}</>;
}

function JsonBlock({ value }: { value: JsonValue }) {
  return <pre className="code-block"><code>{JSON.stringify(value, null, 2)}</code></pre>;
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

function shortDigest(value: string): string {
  return value.length <= 16 ? value : `${value.slice(0, 12)}…${value.slice(-4)}`;
}
