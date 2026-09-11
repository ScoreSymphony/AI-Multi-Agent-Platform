import { useEffect, useState } from "react";
import type { APImanifest } from "../../api/types";
import { useRouter } from "../router";
import { useShellClients } from "./clients";
import { ShellLayout } from "./ShellLayout";
import { type ManifestState } from "./manifest";
import { renderShellRoute } from "./routes";

export function Shell() {
  const { path } = useRouter();
  const baseUrl = import.meta.env.VITE_CONTROL_PLANE_URL ?? "";
  const clients = useShellClients(baseUrl);
  const [manifest, setManifest] = useState<APImanifest | null>(null);
  const [manifestState, setManifestState] = useState<ManifestState>("loading");
  const [menuOpen, setMenuOpen] = useState(false);

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
