export interface NavigationItem {
  label: string;
  path: string;
  group: "Work" | "Agents" | "Data" | "Intelligence" | "Platform" | "Operations";
  apiResource?: string;
}

export const navigation: NavigationItem[] = [
  { label: "Home", path: "/", group: "Work" },
  { label: "First-run onboarding", path: "/onboarding", group: "Work", apiResource: "onboarding" },
  { label: "Chat", path: "/chat", group: "Work" },
  { label: "Projects & Workspaces", path: "/projects", group: "Work", apiResource: "projects" },
  { label: "Repositories", path: "/repositories", group: "Work", apiResource: "repositories" },
  { label: "Tasks", path: "/tasks", group: "Work", apiResource: "tasks" },
  { label: "Runs", path: "/runs", group: "Work", apiResource: "runs" },
  { label: "Templates", path: "/templates", group: "Work", apiResource: "templates" },
  { label: "Agents", path: "/agents", group: "Agents", apiResource: "agents" },
  { label: "Agent Teams", path: "/agent-teams", group: "Agents", apiResource: "agent-teams" },
  { label: "Verification", path: "/verification", group: "Agents", apiResource: "verifications" },
  { label: "Organizations", path: "/organizations", group: "Agents", apiResource: "organizations" },
  { label: "Files & Artifacts", path: "/files", group: "Data", apiResource: "artifacts" },
  { label: "Memory", path: "/memory", group: "Data", apiResource: "memory" },
  { label: "Knowledge", path: "/knowledge", group: "Data", apiResource: "knowledge" },
  { label: "Search", path: "/search", group: "Data", apiResource: "search" },
  { label: "Import / Export", path: "/import-export", group: "Data" },
  { label: "Tools", path: "/tools", group: "Intelligence", apiResource: "capabilities" },
  { label: "Integrations", path: "/integrations", group: "Intelligence", apiResource: "connector-definitions" },
  { label: "Models", path: "/models", group: "Intelligence", apiResource: "models" },
  { label: "Evaluations", path: "/evaluations", group: "Intelligence", apiResource: "evaluation-suites" },
  { label: "Marketplace", path: "/marketplace", group: "Intelligence", apiResource: "registry-items" },
  { label: "Compute", path: "/compute", group: "Platform", apiResource: "nodes" },
  { label: "Terminal", path: "/terminal", group: "Platform", apiResource: "terminal-sessions" },
  { label: "Automations", path: "/automations", group: "Platform", apiResource: "automations" },
  { label: "Plugins", path: "/plugins", group: "Platform", apiResource: "plugins" },
  { label: "Approvals", path: "/approvals", group: "Operations", apiResource: "approvals" },
  { label: "Notifications", path: "/notifications", group: "Operations", apiResource: "notifications" },
  { label: "Events", path: "/events", group: "Operations", apiResource: "timeline" },
  { label: "Observability", path: "/observability", group: "Operations", apiResource: "timeline" },
  { label: "Usage & Limits", path: "/usage", group: "Operations", apiResource: "usage-aggregates" },
  { label: "Settings", path: "/settings", group: "Operations" },
];
