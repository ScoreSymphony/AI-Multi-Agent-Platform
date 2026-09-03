import { useEffect, useMemo, useState } from "react";
import { ControlPlaneClient } from "../api/client";
import type { APImanifest } from "../api/types";
import { PermissionHintsProvider } from "../security/permissions";
import { navigation } from "./navigation";
import { AppLink, matchPath, useRouter } from "./router";
import {
  OverviewPage,
  RunDetailPage,
  RunsPage,
  TaskDetailPage,
  TasksPage,
  UnavailablePage,
} from "../pages/Pages";

export function Shell() {
  const { path } = useRouter();
  const client = useMemo(
    () => new ControlPlaneClient({ baseUrl: import.meta.env.VITE_CONTROL_PLANE_URL ?? "" }),
    [],
  );
  const [manifest, setManifest] = useState<APImanifest | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    void client.manifest().then(setManifest).catch(() => setManifest(null));
  }, [client]);
  useEffect(() => setMenuOpen(false), [path]);

  const taskMatch = matchPath("/tasks/:taskId", path);
  const runMatch = matchPath("/runs/:runId", path);
  const navItem = navigation.find((item) => item.path === path);
  let content;
  if (path === "/") content = <OverviewPage client={client} />;
  else if (path === "/tasks") content = <TasksPage client={client} />;
  else if (taskMatch) content = <TaskDetailPage client={client} taskId={taskMatch.taskId} />;
  else if (path === "/runs") content = <RunsPage client={client} />;
  else if (runMatch) content = <RunDetailPage client={client} runId={runMatch.runId} />;
  else if (navItem) content = <UnavailablePage item={navItem} manifest={manifest} />;
  else content = <UnavailablePage item={{ label: "Unknown route" }} manifest={manifest} />;

  const groups = Array.from(new Set(navigation.map((item) => item.group)));
  return (
    <PermissionHintsProvider>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="app-shell">
        <aside className={menuOpen ? "sidebar sidebar-open" : "sidebar"}>
          <div className="brand"><span className="brand-mark">A</span><div><strong>Agent Platform</strong><small>Control Plane UI</small></div></div>
          <nav aria-label="Platform navigation">
            {groups.map((group) => <div className="nav-group" key={group}><span>{group}</span>{navigation.filter((item) => item.group === group).map((item) => <AppLink className={item.path === path ? "active" : undefined} href={item.path} key={item.path}>{item.label}</AppLink>)}</div>)}
          </nav>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <button className="menu-button" aria-expanded={menuOpen} aria-label="Toggle navigation" onClick={() => setMenuOpen((value) => !value)}>Menu</button>
            <div className="api-indicator"><span className={manifest ? "dot dot-ready" : "dot"} />{manifest ? `/api/${manifest.api_version}` : "API unavailable"}</div>
          </header>
          <main id="main">{content}</main>
        </div>
      </div>
    </PermissionHintsProvider>
  );
}
