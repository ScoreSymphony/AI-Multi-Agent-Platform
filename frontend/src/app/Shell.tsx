import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ControlPlaneClient } from "../api/client";
import type { ReferenceCollection } from "../api/references";
import type { APImanifest } from "../api/types";
import { LoadingState } from "../components/States";
import { PermissionHintsProvider } from "../security/permissions";
import { navigation } from "./navigation";
import { AppLink, matchPath, useRouter } from "./router";
import {
  AgentDetailPage,
  AgentsPage,
  AgentTeamDetailPage,
  AgentTeamsPage,
} from "../pages/AgentsPage";
import {
  CapabilitiesPage,
  CapabilityDetailPage,
  CapabilityProviderDetailPage,
} from "../pages/CapabilitiesPage";
import {
  OverviewPage,
  RunDetailPage,
  UnavailablePage,
} from "../pages/Pages";
import { RunsPage } from "../pages/RunListPage";
import { ModelDetailPage, ModelProviderDetailPage } from "../pages/ModelPages";
import { ModelsPage } from "../pages/ModelInventoryPage";
import { ObservabilityPage } from "../pages/ObservabilityPage";
import { ProjectDetailPage, WorkspaceDetailPage } from "../pages/ProjectPages";
import { ProjectsPage } from "../pages/ProjectListPage";
import { ReferenceDetailPage, ReferencesPage } from "../pages/ReferencePages";
import { SearchPage } from "../pages/SearchPage";
import { TaskDetailPage } from "../pages/TaskDetailPage";
import { ManagedTasksPage, TaskManagementDetailPage } from "../pages/TaskManagementPages";
import { TerminalPage } from "../pages/TerminalPage";
import { UsagePage } from "../pages/UsagePage";

export type ManifestState = "loading" | "ready" | "unavailable";
export type ManifestResourceState = "loading" | "available" | "unavailable";

export function Shell() {
  const { path } = useRouter();
  const client = useMemo(
    () => new ControlPlaneClient({ baseUrl: import.meta.env.VITE_CONTROL_PLANE_URL ?? "" }),
    [],
  );
  const [manifest, setManifest] = useState<APImanifest | null>(null);
  const [manifestState, setManifestState] = useState<ManifestState>("loading");
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    void client
      .manifest()
      .then((loadedManifest) => {
        setManifest(loadedManifest);
        setManifestState("ready");
      })
      .catch(() => {
        setManifest(null);
        setManifestState("unavailable");
      });
  }, [client]);
  useEffect(() => setMenuOpen(false), [path]);

  const projectMatch = matchPath("/projects/:projectId", path);
  const workspaceMatch = matchPath("/workspaces/:workspaceId", path);
  const taskManagementMatch = matchPath("/tasks/:taskId/manage", path);
  const taskMatch = matchPath("/tasks/:taskId", path);
  const runMatch = matchPath("/runs/:runId", path);
  const agentMatch = matchPath("/agents/:agentId", path);
  const agentTeamMatch = matchPath("/agent-teams/:teamId", path);
  const capabilityProviderMatch = matchPath("/tools/providers/:providerId", path);
  const capabilityMatch = matchPath("/tools/:capabilityId", path);
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
  else if (path === "/agents") {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Agents"
        resource="agents"
      >
        <AgentsPage client={client} />
      </ManifestResourcePage>
    );
  } else if (agentMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Agents"
        resource="agents"
      >
        <AgentDetailPage client={client} agentId={agentMatch.agentId} />
      </ManifestResourcePage>
    );
  } else if (path === "/agent-teams") {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Agent Teams"
        resource="agent-teams"
      >
        <AgentTeamsPage client={client} />
      </ManifestResourcePage>
    );
  } else if (agentTeamMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Agent Teams"
        resource="agent-teams"
      >
        <AgentTeamDetailPage client={client} teamId={agentTeamMatch.teamId} />
      </ManifestResourcePage>
    );
  } else if (path === "/files") content = <ReferencesPage client={client} />;
  else if (referenceMatch) content = <ReferenceDetailPage client={client} collection={referenceMatch.collection} resourceId={referenceMatch.resourceId} />;
  else if (path === "/search") content = <SearchPage client={client} />;
  else if (path === "/tools") {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Tools"
        resource="capabilities"
      >
        <CapabilitiesPage client={client} />
      </ManifestResourcePage>
    );
  } else if (capabilityProviderMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Tools"
        resource="capability-providers"
      >
        <CapabilityProviderDetailPage client={client} providerId={capabilityProviderMatch.providerId} />
      </ManifestResourcePage>
    );
  } else if (capabilityMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Tools"
        resource="capabilities"
      >
        <CapabilityDetailPage client={client} capabilityId={capabilityMatch.capabilityId} />
      </ManifestResourcePage>
    );
  } else if (path === "/models") content = <ModelsPage client={client} />;
  else if (providerMatch) content = <ModelProviderDetailPage client={client} providerId={providerMatch.providerId} />;
  else if (modelMatch) content = <ModelDetailPage client={client} modelId={modelMatch.modelId} />;
  else if (path === "/terminal") {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Terminal"
        resource="terminal-sessions"
      >
        <TerminalPage client={client} />
      </ManifestResourcePage>
    );
  } else if (path === "/events") content = <ObservabilityPage client={client} view="events" />;
  else if (path === "/observability") content = <ObservabilityPage client={client} view="observability" />;
  else if (path === "/usage") content = <UsagePage client={client} manifest={manifest} />;
  else if (navItem) content = <UnavailablePage item={navItem} manifest={manifest} />;
  else content = <UnavailablePage item={{ label: "Unknown route" }} manifest={manifest} />;

  const groups = Array.from(new Set(navigation.map((item) => item.group)));
  const apiReady = manifestState === "ready" && manifest !== null;
  return (
    <PermissionHintsProvider>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="app-shell">
        <aside
          id="platform-navigation"
          className={menuOpen ? "sidebar sidebar-open" : "sidebar"}
        >
          <div className="brand"><span className="brand-mark">A</span><div><strong>Agent Platform</strong><small>Control Plane UI</small></div></div>
          <nav aria-label="Platform navigation">
            {groups.map((group) => (
              <div className="nav-group" key={group}>
                <span>{group}</span>
                {navigation.filter((item) => item.group === group).map((item) => {
                  const active = item.path === path;
                  return (
                    <AppLink
                      aria-current={active ? "page" : undefined}
                      className={active ? "active" : undefined}
                      href={item.path}
                      key={item.path}
                    >
                      {item.label}
                    </AppLink>
                  );
                })}
              </div>
            ))}
          </nav>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <button
              className="menu-button"
              aria-controls="platform-navigation"
              aria-expanded={menuOpen}
              aria-label="Toggle navigation"
              onClick={() => setMenuOpen((value) => !value)}
            >
              Menu
            </button>
            <div className="api-indicator" role="status" aria-live="polite">
              <span className={apiReady ? "dot dot-ready" : "dot"} />
              {apiStatusLabel(manifestState, manifest)}
            </div>
          </header>
          <main id="main" tabIndex={-1}>{content}</main>
        </div>
      </div>
    </PermissionHintsProvider>
  );
}

function ManifestResourcePage({
  state,
  manifest,
  label,
  resource,
  children,
}: {
  state: ManifestState;
  manifest: APImanifest | null;
  label: string;
  resource: string;
  children: ReactNode;
}) {
  const resourceState = manifestResourceState(state, manifest, resource);
  if (resourceState === "loading") {
    return <LoadingState label={`Checking ${label} availability…`} />;
  }
  if (resourceState === "unavailable") {
    return <UnavailablePage item={{ label, apiResource: resource }} manifest={manifest} />;
  }
  return children;
}

export function manifestResourceState(
  state: ManifestState,
  manifest: APImanifest | null,
  resource: string,
): ManifestResourceState {
  if (state === "loading") return "loading";
  if (state !== "ready" || manifest === null) return "unavailable";
  return manifest.resources.includes(resource) ? "available" : "unavailable";
}

export function apiStatusLabel(state: ManifestState, manifest: APImanifest | null): string {
  if (state === "ready" && manifest !== null) return `/api/${manifest.api_version}`;
  if (state === "unavailable") return "API unavailable";
  return "Checking API";
}

function referenceRoute(path: string): { collection: ReferenceCollection; resourceId: string } | null {
  for (const collection of ["artifacts", "results", "plans", "steps"] as const) {
    const match = matchPath(`/${collection}/:resourceId`, path);
    if (match) return { collection, resourceId: match.resourceId };
  }
  return null;
}
