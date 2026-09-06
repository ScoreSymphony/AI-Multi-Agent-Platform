import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  BrowserSessionClient,
  type AuthenticatedActor,
  type BrowserSessionSummary,
  type ReleaseOperatorStatus,
} from "../api/browserSession";
import { ControlPlaneError } from "../api/client";
import { Card, EmptyState, ErrorState, LoadingState, StatusBadge } from "../components/States";

export function SettingsPage({ session }: { session: BrowserSessionClient }) {
  const [actor, setActor] = useState<AuthenticatedActor | null>(null);
  const [sessions, setSessions] = useState<BrowserSessionSummary[] | null>(null);
  const [releaseStatus, setReleaseStatus] = useState<ReleaseOperatorStatus | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [releaseError, setReleaseError] = useState<unknown>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mutating, setMutating] = useState(false);

  const loadReleaseStatus = useCallback(async () => {
    try {
      setReleaseStatus(await session.releaseStatus());
      setReleaseError(null);
    } catch (nextError) {
      setReleaseStatus(null);
      setReleaseError(nextError);
    }
  }, [session]);

  const loadIdentity = useCallback(async () => {
    setChecking(true);
    try {
      const currentActor = await session.me();
      setActor(currentActor);
      setSessions(await session.listSessions());
      setError(null);
      await loadReleaseStatus();
    } catch (nextError) {
      if (nextError instanceof ControlPlaneError && nextError.status === 401) {
        setActor(null);
        setSessions(null);
        setReleaseStatus(null);
        setReleaseError(null);
        session.clearLocalSession();
        setError(null);
      } else {
        setError(nextError);
      }
    } finally {
      setChecking(false);
    }
  }, [loadReleaseStatus, session]);

  useEffect(() => {
    void loadIdentity();
  }, [loadIdentity]);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMutating(true);
    try {
      const result = await session.login(username, password);
      setActor(result.actor);
      setPassword("");
      setSessions(await session.listSessions());
      await loadReleaseStatus();
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setMutating(false);
    }
  }

  async function logout() {
    setMutating(true);
    try {
      await session.logout();
      setActor(null);
      setSessions(null);
      setReleaseStatus(null);
      setReleaseError(null);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setMutating(false);
    }
  }

  async function renew() {
    setMutating(true);
    try {
      await session.renew();
      await loadIdentity();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setMutating(false);
    }
  }

  async function revoke(sessionId: string) {
    setMutating(true);
    try {
      await session.revokeSession(sessionId);
      await loadIdentity();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setMutating(false);
    }
  }

  if (checking && actor === null) return <LoadingState label="Checking browser session…" />;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Authentication, session & platform status</p>
        <h1>Settings</h1>
        <p>
          Browser authentication is established by the platform-owned Control Plane. The UI keeps
          the HttpOnly session cookie opaque and stores only the CSRF token required for same-origin
          state changes.
        </p>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void loadIdentity()} /> : null}

      {actor === null ? (
        <Card title="Sign in">
          <form className="stack" onSubmit={login}>
            <label>
              Username
              <input
                autoComplete="username"
                disabled={mutating}
                onChange={(event) => setUsername(event.target.value)}
                required
                value={username}
              />
            </label>
            <label>
              Password
              <input
                autoComplete="current-password"
                disabled={mutating}
                onChange={(event) => setPassword(event.target.value)}
                required
                type="password"
                value={password}
              />
            </label>
            <div className="actions">
              <button disabled={mutating || !username.trim() || !password} type="submit">
                {mutating ? "Signing in…" : "Sign in"}
              </button>
            </div>
          </form>
        </Card>
      ) : (
        <>
          <div className="grid-two">
            <Card title="Current identity">
              <dl className="definition-list">
                <div><dt>Actor</dt><dd><code>{actor.actor_id}</code></dd></div>
                <div><dt>Type</dt><dd>{actor.actor_type}</dd></div>
                <div><dt>Method</dt><dd>{actor.authentication_method}</dd></div>
                <div><dt>Organization</dt><dd>{actor.organization_id ?? "—"}</dd></div>
                <div><dt>Project</dt><dd>{actor.project_id ?? "—"}</dd></div>
                <div><dt>Expires</dt><dd>{formatDate(actor.expires_at)}</dd></div>
              </dl>
            </Card>
            <Card title="Session controls">
              <p>
                Authentication establishes identity only. Authorization and approval decisions remain
                server-side under the canonical #15 boundary.
              </p>
              <div className="actions">
                <button disabled={mutating} onClick={() => void renew()}>Renew session</button>
                <button disabled={mutating} onClick={() => void logout()}>Sign out</button>
              </div>
            </Card>
          </div>

          <Card title="Platform release & upstream updates">
            {releaseError ? (
              <ErrorState error={releaseError} onRetry={() => void loadReleaseStatus()} />
            ) : releaseStatus === null ? (
              <LoadingState label="Loading release status…" />
            ) : (
              <div className="stack">
                <dl className="definition-list">
                  <div><dt>Platform release</dt><dd><code>{releaseStatus.platform_release}</code></dd></div>
                  <div>
                    <dt>Release readiness</dt>
                    <dd>{releaseStatus.release_ready === null ? "No formal manifest loaded" : releaseStatus.release_ready ? "Ready" : "Blocked"}</dd>
                  </div>
                  <div><dt>Update discovery</dt><dd><StatusBadge value={releaseStatus.update_discovery.mode} /></dd></div>
                  <div><dt>Last observation</dt><dd>{formatDate(releaseStatus.update_discovery.observed_at)}</dd></div>
                  <div><dt>Automatic production updates</dt><dd>{releaseStatus.automatic_production_updates ? "Enabled" : "Disabled"}</dd></div>
                  <div><dt>Inventory reviewed</dt><dd>{formatDate(releaseStatus.compatibility_inventory.last_reviewed_at)}</dd></div>
                </dl>

                {releaseStatus.operator_warnings.length > 0 ? (
                  <div>
                    <strong>Operator warnings</strong>
                    <ul>
                      {releaseStatus.operator_warnings.map((warning) => <li key={warning}>{warning}</li>)}
                    </ul>
                  </div>
                ) : null}

                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Component</th>
                        <th>Pinned revision</th>
                        <th>Compatibility</th>
                        <th>Latest known</th>
                        <th>Checked</th>
                        <th>Risk</th>
                      </tr>
                    </thead>
                    <tbody>
                      {releaseStatus.compatibility_inventory.components.map((item) => (
                        <tr key={item.component}>
                          <td>{item.component}</td>
                          <td><code>{item.revision}</code></td>
                          <td><StatusBadge value={item.status} /></td>
                          <td><code>{item.latest_known_revision}</code></td>
                          <td>{formatDate(item.last_checked_at)}</td>
                          <td><StatusBadge value={item.update_risk} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {releaseStatus.update_discovery.update_available ? (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Candidate</th>
                          <th>Revision</th>
                          <th>Status</th>
                          <th>Classification</th>
                          <th>Review</th>
                        </tr>
                      </thead>
                      <tbody>
                        {releaseStatus.update_discovery.candidates
                          .filter((item) => !["current", "not_checked", "disabled", "offline"].includes(item.disposition))
                          .map((item) => (
                            <tr key={`${item.component}:${item.candidate_revision ?? "none"}`}>
                              <td>{item.component}</td>
                              <td><code>{item.candidate_revision ?? "—"}</code></td>
                              <td><StatusBadge value={item.disposition} /></td>
                              <td>{item.classifications.join(", ") || "unknown"}</td>
                              <td>{item.manual_review_required ? "Required" : "Normal update PR"}</td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p>
                    No actionable upstream candidate is present in the current advisory report.
                    Discovery never changes production pins from this page.
                  </p>
                )}
              </div>
            )}
          </Card>

          <Card title="Browser sessions">
            {sessions === null ? (
              <LoadingState />
            ) : sessions.length === 0 ? (
              <EmptyState title="No browser sessions" />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Session</th><th>Status</th><th>Authenticated</th><th>Last seen</th><th>Expires</th><th>Action</th></tr>
                  </thead>
                  <tbody>
                    {sessions.map((item) => (
                      <tr key={item.id}>
                        <td><code>{item.id}</code></td>
                        <td><StatusBadge value={item.active ? "active" : "inactive"} /></td>
                        <td>{formatDate(item.authenticated_at)}</td>
                        <td>{formatDate(item.last_seen_at)}</td>
                        <td>{formatDate(item.expires_at)}</td>
                        <td>
                          <button
                            disabled={mutating || !item.active}
                            onClick={() => void revoke(item.id)}
                          >
                            Revoke
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
