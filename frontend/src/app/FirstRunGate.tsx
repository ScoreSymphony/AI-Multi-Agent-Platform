import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  BrowserSessionClient,
  type BootstrapStatus,
} from "../api/browserSession";
import { ControlPlaneError } from "../api/transport";
import { Card, ErrorState, LoadingState } from "../components/States";
import { useRouter } from "./router";

type AccessState =
  | { kind: "checking" }
  | { kind: "bootstrap"; status: BootstrapStatus }
  | { kind: "login" }
  | { kind: "authenticated" }
  | { kind: "error"; error: unknown };

export function FirstRunGate({ children }: { children: ReactNode }) {
  const { navigate } = useRouter();
  const baseUrl = import.meta.env.VITE_CONTROL_PLANE_URL ?? "";
  const session = useMemo(() => new BrowserSessionClient({ baseUrl }), [baseUrl]);
  const [state, setState] = useState<AccessState>({ kind: "checking" });
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "checking" });
    setActionError(null);
    try {
      const status = await session.bootstrapStatus();
      if (status.state === "uninitialized" && status.bootstrap_available) {
        session.clearLocalSession();
        setState({ kind: "bootstrap", status });
        return;
      }

      try {
        await session.me();
        setState({ kind: "authenticated" });
      } catch (error) {
        if (error instanceof ControlPlaneError && error.status === 401) {
          session.clearLocalSession();
          setState({ kind: "login" });
          return;
        }
        throw error;
      }
    } catch (error) {
      setState({ kind: "error", error });
    }
  }, [session]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function bootstrap(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const username = requiredText(form, "username");
    const password = requiredText(form, "password");
    const confirmation = requiredText(form, "password_confirmation");
    if (password !== confirmation) {
      setActionError(new Error("Password confirmation does not match."));
      return;
    }

    setBusy(true);
    setActionError(null);
    try {
      const result = await session.bootstrapAdmin(username, password, confirmation);
      if (!result.authorization_granted) {
        throw new Error("The initial administrator policy was not granted.");
      }
      setState({ kind: "authenticated" });
      navigate("/onboarding");
    } catch (error) {
      setActionError(error);
      if (error instanceof ControlPlaneError && error.status === 409) {
        await refresh();
      }
    } finally {
      setBusy(false);
    }
  }

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setActionError(null);
    try {
      await session.login(requiredText(form, "username"), requiredText(form, "password"));
      setState({ kind: "authenticated" });
      navigate("/");
    } catch (error) {
      setActionError(error);
    } finally {
      setBusy(false);
    }
  }

  if (state.kind === "authenticated") return <>{children}</>;
  if (state.kind === "checking") return <FirstRunFrame><LoadingState label="Checking platform setup…" /></FirstRunFrame>;
  if (state.kind === "error") {
    return <FirstRunFrame><ErrorState error={state.error} onRetry={() => void refresh()} /></FirstRunFrame>;
  }
  if (state.kind === "bootstrap") {
    return (
      <FirstRunFrame>
        <BootstrapAccountForm
          status={state.status}
          busy={busy}
          error={actionError}
          onSubmit={bootstrap}
        />
      </FirstRunFrame>
    );
  }
  return (
    <FirstRunFrame>
      <LoginForm busy={busy} error={actionError} onSubmit={login} />
    </FirstRunFrame>
  );
}

export function BootstrapAccountForm({
  status,
  busy,
  error,
  onSubmit,
}: {
  status: BootstrapStatus;
  busy: boolean;
  error: unknown;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <Card title="Create the first administrator account">
      <div className="stack">
        <p>
          This platform has no local user yet. Create the first administrator to continue
          directly into guided setup. No terminal bootstrap command is required.
        </p>
        {error ? <ErrorState error={error} /> : null}
        <form className="stack" onSubmit={onSubmit}>
          <label>
            Username
            <input
              autoComplete="username"
              disabled={busy}
              name="username"
              required
            />
          </label>
          <label>
            Password
            <input
              autoComplete="new-password"
              disabled={busy}
              minLength={status.password_policy.minimum_length}
              name="password"
              required
              type="password"
            />
          </label>
          <label>
            Confirm password
            <input
              autoComplete="new-password"
              disabled={busy}
              minLength={status.password_policy.minimum_length}
              name="password_confirmation"
              required
              type="password"
            />
          </label>
          <p>
            Password requirement: at least {status.password_policy.minimum_length} characters
            and at most {status.password_policy.maximum_bytes} UTF-8 bytes.
          </p>
          <button disabled={busy} type="submit">
            {busy ? "Creating account…" : "Create account and continue"}
          </button>
        </form>
      </div>
    </Card>
  );
}

export function LoginForm({
  busy,
  error,
  onSubmit,
}: {
  busy: boolean;
  error: unknown;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <Card title="Sign in">
      <div className="stack">
        <p>The platform is initialized. Sign in with an existing local account.</p>
        {error ? <ErrorState error={error} /> : null}
        <form className="stack" onSubmit={onSubmit}>
          <label>
            Username
            <input autoComplete="username" disabled={busy} name="username" required />
          </label>
          <label>
            Password
            <input
              autoComplete="current-password"
              disabled={busy}
              name="password"
              required
              type="password"
            />
          </label>
          <button disabled={busy} type="submit">
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </Card>
  );
}

function FirstRunFrame({ children }: { children: ReactNode }) {
  return (
    <main className="content" aria-label="Platform access">
      <div className="stack" style={{ maxWidth: "40rem", margin: "4rem auto", padding: "1rem" }}>
        <header className="page-header">
          <p className="eyebrow">AI Multi-Agent Platform</p>
          <h1>Platform setup</h1>
        </header>
        {children}
      </div>
    </main>
  );
}

function requiredText(form: FormData, field: string): string {
  const value = form.get(field);
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${field} is required`);
  }
  return value;
}
