import { useState, type FormEvent } from "react";
import type { BrowserSessionClient } from "../api/browserSession";
import { Card, ErrorState } from "../components/States";
import { BrowserAuthenticationTransportNotice } from "../security/BrowserAuthenticationTransportNotice";
import {
  currentBrowserAuthenticationTransport,
  type BrowserAuthenticationTransport,
} from "../security/browserAuthenticationTransport";

export function SignInPage({
  session,
  onAuthenticated,
  transport,
}: {
  session: BrowserSessionClient;
  onAuthenticated: () => Promise<void> | void;
  transport?: BrowserAuthenticationTransport;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const authenticationTransport = transport ?? currentBrowserAuthenticationTransport();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      await session.login(username, password);
      setPassword("");
      await onAuthenticated();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="stack" aria-labelledby="sign-in-title">
      <header className="page-header">
        <p className="eyebrow">Welcome back</p>
        <h1 id="sign-in-title">Sign in</h1>
        <p>
          This installation is already initialized. Sign in with an existing local account to
          continue; incomplete initial setup resumes automatically after authentication.
        </p>
      </header>

      {!authenticationTransport.supported ? (
        <BrowserAuthenticationTransportNotice transport={authenticationTransport} />
      ) : (
        <>
          {error ? <ErrorState error={error} /> : null}

          <Card title="Local account">
            <form className="stack" onSubmit={submit}>
          <label>
            Username
            <input
              autoComplete="username"
              autoFocus
              disabled={busy}
              onChange={(event) => setUsername(event.target.value)}
              required
              value={username}
            />
          </label>
          <label>
            Password
            <input
              autoComplete="current-password"
              disabled={busy}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          <div className="actions">
            <button disabled={busy || !username.trim() || !password} type="submit">
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </div>
            </form>
          </Card>
        </>
      )}
    </main>
  );
}
