export interface NavigationItem {
  label: string;
  path: string;
  group: "Work" | "Agents" | "Data" | "Intelligence" | "Platform" | "Operations";
  apiResource?: string;
}

export type NavigationMaturity = "beta" | "experimental";

export const navigationMaturity: Partial<Record<string, NavigationMaturity>> = {
  "/learning": "experimental",
};

export const navigation: NavigationItem[] = [
  { label: "Home", path: "/", group: "Work" },
  { label: "Chat", path: "/chat", group: "Work" },
  { label: "Projects & Workspaces", path: "/projects", group: "Work", apiResource: "projects" },
  { label: "Repositories", path: "/repositories", group: "Work", apiResource: "repositories" },
  { label: "Tasks", path: "/tasks", group: "Work", apiResource: "tasks" },
  { label: "Goals", path: "/goals", group: "Work", apiResource: "goals" },
  { label: "Runs", path: "/runs", group: "Work" },
  { label: "Templates", path: "/templates", group: "Work", apiResource: "templates" },
  { label: "Agents", path: "/agents", group: "Agents", apiResource: "agents" },
  { label: "Agent Teams", path: "/agent-teams", group: "Agents", apiResource: "agent-teams" },
  { label: "Verification", path: "/verification", group: "Agents", apiResource: "verifications" },
  { label: "Files & Artifacts", path: "/files", group: "Data", apiResource: "files" },
  { label: "Memory", path: "/memory", group: "Data", apiResource: "memory" },
  { label: "Knowledge", path: "/knowledge", group: "Data", apiResource: "knowledge" },
  { label: "Search", path: "/search", group: "Data", apiResource: "search" },
  { label: "Import / Export", path: "/import-export", group: "Data", apiResource: "portability-packages" },
  { label: "Tools", path: "/tools", group: "Intelligence", apiResource: "capabilities" },
  { label: "Integrations", path: "/integrations", group: "Intelligence", apiResource: "connector-definitions" },
  { label: "Models", path: "/models", group: "Intelligence", apiResource: "models" },
  { label: "Evaluations", path: "/evaluations", group: "Intelligence", apiResource: "evaluation-suites" },
  { label: "Learning", path: "/learning", group: "Intelligence", apiResource: "learning-candidates" },
  { label: "Marketplace", path: "/marketplace", group: "Intelligence", apiResource: "registry-items" },
  { label: "Compute", path: "/compute", group: "Platform", apiResource: "nodes" },
  { label: "Applications", path: "/applications", group: "Platform", apiResource: "applications" },
  { label: "Terminal", path: "/terminal", group: "Platform", apiResource: "terminal-sessions" },
  { label: "Automations", path: "/automations", group: "Platform", apiResource: "automations" },
  { label: "Plugins", path: "/plugins", group: "Platform", apiResource: "plugins" },
  { label: "Approvals", path: "/approvals", group: "Operations", apiResource: "approvals" },
  { label: "Governance", path: "/governance", group: "Operations", apiResource: "proposals" },
  { label: "Organizations", path: "/organizations", group: "Operations", apiResource: "organizations" },
  { label: "Notifications", path: "/notifications", group: "Operations", apiResource: "notifications" },
  { label: "Events", path: "/events", group: "Operations", apiResource: "timeline" },
  { label: "Observability", path: "/observability", group: "Operations", apiResource: "timeline" },
  { label: "Usage & Limits", path: "/usage", group: "Operations", apiResource: "usage-aggregates" },
  { label: "Onboarding", path: "/onboarding", group: "Operations", apiResource: "onboarding" },
  { label: "Settings", path: "/settings", group: "Operations" },
];


const DETAIL_ROUTE_PARENTS: ReadonlyArray<readonly [string, string]> = [
  ["/workspaces/", "/projects"],
  ["/plans/", "/files"],
  ["/steps/", "/files"],
  ["/artifacts/", "/files"],
  ["/results/", "/files"],
  ["/workflows/", "/templates"],
  ["/capability-assignments/", "/templates"],
  ["/model-routing-profiles/", "/templates"],
];

export function navigationItemForPath(path: string): NavigationItem | undefined {
  const exact = navigation.find((item) => item.path === path);
  if (exact) return exact;

  const alias = DETAIL_ROUTE_PARENTS.find(([prefix]) => path.startsWith(prefix));
  if (alias) return navigation.find((item) => item.path === alias[1]);

  return navigation
    .filter((item) => item.path !== "/" && path.startsWith(`${item.path}/`))
    .sort((left, right) => right.path.length - left.path.length)[0];
}
