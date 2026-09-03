import { useEffect, useMemo, useState } from "react";
import { ControlPlaneClient } from "../api/client";
import type { ReferenceCollection } from "../api/references";
import type { APImanifest } from "../api/types";
import { PermissionHintsProvider } from "../security/permissions";
import { navigation } from "./navigation";
import { AppLink, matchPath, useRouter } from "./router";
import {
  OverviewPage,
  RunDetailPage,
  TaskDetailPage,
  UnavailablePage,
} from "../pages/Pages";
import { RunsPage } from "../pages/RunListPage";
import {
  ModelDetailPage,
  ModelProviderDetailPage,
  ModelsPage,
} from "../pages/ModelPages";
import { ObservabilityPage } from "../pages/ObservabilityPage";
import { ProjectDetailPage, ProjectsPage, WorkspaceDetailPage } from "../pages/ProjectPages";
import { ReferenceDetailPage, ReferencesPage } from "../pages/ReferencePages";
import { ManagedTasksPage, TaskManagementDetailPage } from "../pages/TaskManagementPages";
import { TerminalPage } from "../pages/TerminalPage";
import { UsagePage } from "../pages/UsagePage";

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

  const projectMatch = matchPath("/projects/:projectId", path);
  const workspaceMatch = matchPath("/workspaces/:workspaceId", path);
  const taskManagementMatch = matchPath("/tasks/:taskId/manage", path);
  const taskMatch = matchPath("/tasks/:taskId", path);
  const runMatch = matchPath("/runs/:runId", path);
  const providerMatch = matchPath("/models/providers/:providerId", path);
  const modelMatch = matchPath("/models/:modelId", path);
  const referenceMatch = referenceRoute(path);
  const navItem = navigation.find((item) => item.path === path);
  let content;
  if (path === "/") content = <OverviewPage client={client} />;
  else if (path === "/projects") content = <ProjectsPage client={client} />;
  else if (projectMatch) content = <ProjectDetailPage client={client} projectId={projectMatch.projectId} />;
  else if (workspaceMatch) content = <WorkspaceDetailPage client={client} workspaceId={workspaceMatch.workspaceId} />;
  else if (path === "/tasks") content = <ManagedTasksPage client={client} />;
  else if (taskManagementMatch) content = <TaskManagementDetailPage client={client} taskId={taskManagementMatch.taskId} />;
  else if (taskMatch) content = <TaskDetailPage client={client} taskId={taskMatch.taskId} />;
  else if (path === "/runs") content = <RunsPage client={client} />;
  else if (runMatch) content = <RunDetailPage client={client} runId={runMatch.runId} />;
  else if (path === "/files") content = <ReferencesPage client={client} />;
  else if (referenceMatch) content = <ReferenceDetailPage client={client} collection={referenceMatch.collection} resourceId={referenceMatch.resourceId} />;
  else if (path === "/models") content = <ModelsPage client={client} />;
  else if (providerMatch) content = <ModelProviderDetailPage client={client} providerId={providerMatch.providerId} />;
  else if (modelMatch) content = <ModelDetailPage client={client} modelId={modelMatch.modelId} />;
  else if (path === "/terminal") content = <TerminalPage client={client} />;
  else if (path === "/events") content = <ObservabilityPage client={client} view="events" />;
  else if (path === "/observability") content = <ObservabilityPage client={client} view="observability" />;
  else if (path === "/usage") content = <UsagePage client={client} manifest={manifest} />;
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

function referenceRoute(path: string): { collection: ReferenceCollection; resourceId: string } | null {
  for (const collection of ["artifacts", "results", "plans", "steps"] as const) {
    const match = matchPath(`/${collection}/:resourceId`, path);
    if (match) return { collection, resourceId: match.resourceId };
  }
  return null;
}
