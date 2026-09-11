import type { ReactNode } from "react";
import type { APImanifest } from "../../api/types";
import { OnboardingCallout } from "../../components/OnboardingCallout";
import { PermissionHintsProvider } from "../../security/permissions";
import { navigation } from "../navigation";
import { AppLink } from "../router";
import type { ShellClients } from "./clients";
import { apiStatusLabel, type ManifestState } from "./manifest";

export function ShellLayout({
  path,
  menuOpen,
  onToggleMenu,
  manifest,
  manifestState,
  clients,
  content,
}: {
  path: string;
  menuOpen: boolean;
  onToggleMenu: () => void;
  manifest: APImanifest | null;
  manifestState: ManifestState;
  clients: ShellClients;
  content: ReactNode;
}) {
  const groups = Array.from(new Set(navigation.map((item) => item.group)));
  const apiReady = manifestState === "ready" && manifest !== null;
  const onboardingAvailable = manifest?.resources.includes("onboarding") ?? false;

  return (
    <PermissionHintsProvider>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="app-shell">
        <aside id="platform-navigation" className={menuOpen ? "sidebar sidebar-open" : "sidebar"}>
          <div className="brand"><span className="brand-mark">A</span><div><strong>Agent Platform</strong><small>Control Plane UI</small></div></div>
          <nav aria-label="Platform navigation">
            {groups.map((group) => (
              <div className="nav-group" key={group}>
                <span>{group}</span>
                {navigation.filter((item) => item.group === group).map((item) => {
                  const active = item.path === path;
                  return <AppLink aria-current={active ? "page" : undefined} className={active ? "active" : undefined} href={item.path} key={item.path}>{item.label}</AppLink>;
                })}
              </div>
            ))}
          </nav>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <button className="menu-button" aria-controls="platform-navigation" aria-expanded={menuOpen} aria-label="Toggle navigation" onClick={onToggleMenu}>Menu</button>
            <div className="api-indicator" role="status" aria-live="polite"><span className={apiReady ? "dot dot-ready" : "dot"} />{apiStatusLabel(manifestState, manifest)}</div>
          </header>
          <main id="main" tabIndex={-1}>
            {path !== "/onboarding" && onboardingAvailable ? <OnboardingCallout client={clients.onboardingClient} /> : null}
            {content}
          </main>
        </div>
      </div>
    </PermissionHintsProvider>
  );
}
