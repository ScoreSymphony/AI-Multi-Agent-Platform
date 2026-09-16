import { useCallback, useEffect, useState } from "react";
import type { FirstUserBootstrapStatus } from "../../api/browserSession";
import type { APImanifest } from "../../api/types";
import { ErrorState, LoadingState } from "../../components/States";
import { FirstUserSetupPage } from "../../pages/FirstUserSetupPage";
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

  useEffect(() => {
    void loadBootstrapStatus();
  }, [loadBootstrapStatus]);

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
              if (status?.state === "initialized") navigate("/onboarding");
            });
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
