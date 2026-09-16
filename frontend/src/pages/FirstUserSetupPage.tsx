import { useState, type FormEvent } from "react";
import {
  type BrowserSessionClient,
  type FirstUserBootstrapStatus,
} from "../api/browserSession";
import { Card, ErrorState } from "../components/States";

export function FirstUserSetupPage({
  session,
  status,
  onComplete,
}: {
  session: BrowserSessionClient;
  status: FirstUserBootstrapStatus;
  onComplete: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const passwordBytes = new TextEncoder().encode(password).byteLength;
  const passwordTooShort = password.length < status.password_policy.min_length;
  const passwordTooLong = passwordBytes > status.password_policy.max_bytes;
  const passwordsMatch = password === confirmation;
  const canSubmit =
    !mutating
    && Boolean(username.trim())
    && Boolean(password)
    && Boolean(confirmation)
    && passwordsMatch
    && !passwordTooShort
    && !passwordTooLong;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    setMutating(true);
    setError(null);
    try {
      await session.bootstrapAdmin(username, password);
      setPassword("");
      setConfirmation("");
      onComplete();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setMutating(false);
    }
  }

  const recovering = status.state === "incomplete";

  return (
    <main className="stack" aria-labelledby="first-user-title">
      <header className="page-header">
        <p className="eyebrow">Initial setup</p>
        <h1 id="first-user-title">
          {recovering ? "Resume administrator setup" : "Create your administrator account"}
        </h1>
        <p>
          {recovering
            ? "The first identity was persisted, but setup did not finish. Enter the same account credentials to resume safely."
            : "Create the first local account. The platform will install its explicit administrator policy and open an authenticated browser session automatically."}
        </p>
      </header>

      {error ? <ErrorState error={error} /> : null}

      <Card title={recovering ? "Verify first administrator" : "Administrator account"}>
        <form className="stack" onSubmit={submit}>
          <label>
            Username
            <input
              autoComplete="username"
              autoFocus
              disabled={mutating}
              onChange={(event) => setUsername(event.target.value)}
              required
              value={username}
            />
          </label>
          <label>
            Password
            <input
              autoComplete="new-password"
              disabled={mutating}
              minLength={status.password_policy.min_length}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          <label>
            Confirm password
            <input
              autoComplete="new-password"
              disabled={mutating}
              minLength={status.password_policy.min_length}
              onChange={(event) => setConfirmation(event.target.value)}
              required
              type="password"
              value={confirmation}
            />
          </label>

          <p className="muted">
            Passwords must contain at least {status.password_policy.min_length} characters and no
            more than {status.password_policy.max_bytes} UTF-8 bytes.
          </p>
          {password && passwordTooShort ? (
            <p role="alert">The password is shorter than the server requirement.</p>
          ) : null}
          {password && passwordTooLong ? (
            <p role="alert">The password exceeds the server byte limit.</p>
          ) : null}
          {confirmation && !passwordsMatch ? (
            <p role="alert">The passwords do not match.</p>
          ) : null}

          <div className="actions">
            <button disabled={!canSubmit} type="submit">
              {mutating
                ? "Creating administrator…"
                : recovering
                  ? "Resume setup"
                  : "Create administrator"}
            </button>
          </div>
        </form>
      </Card>
    </main>
  );
}
