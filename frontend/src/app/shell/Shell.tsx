import { useCallback, useEffect, useState } from "react";
import type { FirstUserBootstrapStatus } from "../../api/browserSession";
import { ControlPlaneError } from "../../api/client";
import type { SetupSessionStatus } from "../../api/setup";
import type { APImanifest } from "../../api/types";
import { ErrorState, LoadingState } from "../../components/States";
import { FirstUserSetupPage } from "../../pages/FirstUserSetupPage";
import { SignInPage } from "../../pages/SignInPage";
import { useRouter } from "../router";
import { useShellClients } from "./clients";
import { ShellLayout } from "./ShellLayout";
import { type ManifestState } from "./manifest";
import { renderShellRoute } from "./routes";

export function Shell() {
  const { path, navigate } = useRouter();
  const baseUrl = import.meta.env.VITE_CONTROL_PLANE_URL ?? "";
  const clients = useShellClients(baseUrl);
  const [manifest, setManifest] = useState<APImanifest | null>(null);
  const [manifestState, setManifestState] = useState<ManifestState>("loading");
  const [menuOpen, setMenuOpen] = useState(false);
  const [bootstrapStatus, setBootstrapStatus] = useState<FirstUserBootstrapStatus | null>(null);
  const [bootstrapChecked, setBootstrapChecked] = useState(false);
  const [bootstrapError, setBootstrapError] = useState<unknown>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [sessionError, setSessionError] = useState<unknown>(null);

  const loadBootstrapStatus = useCallback(async () => {
    if (typeof document === "undefined") return null;
    try {
      const status = await clients.session.bootstrapStatus();
      setBootstrapStatus(status);
      setBootstrapError(null);
      return status;
    } catch (error) {
      setBootstrapStatus(null);
      setBootstrapError(error);
      return null;
    } finally {
      setBootstrapChecked(true);
    }
  }, [clients.session]);

  const loadAuthenticatedState = useCallback(async (): Promise<SetupSessionStatus | null> => {
    if (typeof document === "undefined") return null;
    try {
      await clients.session.me();
      setAuthenticated(true);
      const setup = await clients.setupClient.status();
      setSessionError(null);
      return setup;
    } catch (error) {
      if (error instanceof ControlPlaneError && error.status === 401) {
        clients.session.clearLocalSession();
        setAuthenticated(false);
        setSessionError(null);
      } else {
        setAuthenticated(false);
        setSessionError(error);
      }
      return null;
    } finally {
      setSessionChecked(true);
    }
  }, [clients.session, clients.setupClient]);

  useEffect(() => {
    void loadBootstrapStatus();
  }, [loadBootstrapStatus]);

  useEffect(() => {
    if (bootstrapStatus?.state !== "initialized") return;
    void loadAuthenticatedState().then((setup) => {
      if (setup !== null && !setup.readiness.ready && path !== "/onboarding") {
        navigate("/onboarding");
      }
    });
  }, [bootstrapStatus, loadAuthenticatedState, navigate, path]);

  useEffect(() => {
    void clients.client.manifest().then((loadedManifest) => {
      setManifest(loadedManifest);
      setManifestState("ready");
    }).catch(() => {
      setManifest(null);
      setManifestState("unavailable");
    });
  }, [clients.client]);

  useEffect(() => setMenuOpen(false), [path]);

  if (typeof document !== "undefined") {
    if (!bootstrapChecked) {
      return <LoadingState label="Checking initial setup…" />;
    }
    if (bootstrapError) {
      return <ErrorState error={bootstrapError} onRetry={() => void loadBootstrapStatus()} />;
    }
    if (bootstrapStatus && bootstrapStatus.state !== "initialized") {
      return (
        <FirstUserSetupPage
          session={clients.session}
          status={bootstrapStatus}
          onComplete={() => {
            void loadBootstrapStatus().then((status) => {
              if (status?.state !== "initialized") return;
              setAuthenticated(true);
              setSessionChecked(true);
              navigate("/onboarding");
            });
          }}
        />
      );
    }
    if (!sessionChecked) {
      return <LoadingState label="Checking browser session and setup readiness…" />;
    }
    if (sessionError) {
      return <ErrorState error={sessionError} onRetry={() => void loadAuthenticatedState()} />;
    }
    if (!authenticated) {
      return (
        <SignInPage
          session={clients.session}
          onAuthenticated={async () => {
            const setup = await loadAuthenticatedState();
            if (setup === null) return;
            navigate(setup.readiness.ready ? "/" : "/onboarding");
          }}
        />
      );
    }
  }

  const content = renderShellRoute({ path, clients, manifest, manifestState });
  return (
    <ShellLayout
      path={path}
      menuOpen={menuOpen}
      onToggleMenu={() => setMenuOpen((value) => !value)}
      manifest={manifest}
      manifestState={manifestState}
      clients={clients}
      content={content}
    />
  );
}
