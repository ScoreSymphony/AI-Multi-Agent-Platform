import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  BrowserSessionClient,
  type AuthenticatedActor,
  type BrowserSessionSummary,
} from "../api/browserSession";
import { ControlPlaneError } from "../api/client";
import { Card, EmptyState, ErrorState, LoadingState, StatusBadge } from "../components/States";

export function SettingsPage({ session }: { session: BrowserSessionClient }) {
  const [actor, setActor] = useState<AuthenticatedActor | null>(null);
  const [sessions, setSessions] = useState<BrowserSessionSummary[] | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mutating, setMutating] = useState(false);

  const loadIdentity = useCallback(async () => {
    setChecking(true);
    try {
      const currentActor = await session.me();
      setActor(currentActor);
      setSessions(await session.listSessions());
      setError(null);
    } catch (nextError) {
      if (nextError instanceof ControlPlaneError && nextError.status === 401) {
        setActor(null);
        setSessions(null);
        session.clearLocalSession();
        setError(null);
      } else {
        setError(nextError);
      }
    } finally {
      setChecking(false);
    }
  }, [session]);

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
        <p className="eyebrow">Authentication & session</p>
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
