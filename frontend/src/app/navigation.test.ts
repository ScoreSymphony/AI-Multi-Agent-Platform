import { describe, expect, it } from "vitest";
import { navigation, navigationItemForPath, navigationMaturity } from "./navigation";

describe("#1234 V1 Web navigation coverage", () => {
  it("keeps every claimed primary V1 product route discoverable", () => {
    const paths = new Set(navigation.map((item) => item.path));
    for (const path of [
      "/",
      "/chat",
      "/projects",
      "/repositories",
      "/tasks",
      "/goals",
      "/runs",
      "/templates",
      "/agents",
      "/agent-teams",
      "/verification",
      "/files",
      "/memory",
      "/knowledge",
      "/search",
      "/import-export",
      "/tools",
      "/integrations",
      "/models",
      "/evaluations",
      "/learning",
      "/marketplace",
      "/compute",
      "/applications",
      "/terminal",
      "/automations",
      "/plugins",
      "/approvals",
      "/governance",
      "/organizations",
      "/notifications",
      "/events",
      "/observability",
      "/usage",
      "/onboarding",
      "/settings",
    ]) {
      expect(paths.has(path), `missing primary Web route: ${path}`).toBe(true);
    }
  });

  it("binds optional product routes to their canonical Control Plane resources", () => {
    const expected: Record<string, string> = {
      "/onboarding": "onboarding",
      "/repositories": "repositories",
      "/templates": "templates",
      "/agents": "agents",
      "/agent-teams": "agent-teams",
      "/verification": "verifications",
      "/files": "files",
      "/memory": "memory",
      "/knowledge": "knowledge",
      "/search": "search",
      "/import-export": "portability-packages",
      "/tools": "capabilities",
      "/integrations": "connector-definitions",
      "/models": "models",
      "/evaluations": "evaluation-suites",
      "/learning": "learning-candidates",
      "/marketplace": "registry-items",
      "/compute": "nodes",
      "/applications": "applications",
      "/terminal": "terminal-sessions",
      "/automations": "automations",
      "/plugins": "plugins",
      "/approvals": "approvals",
      "/governance": "proposals",
      "/organizations": "organizations",
      "/notifications": "notifications",
      "/events": "timeline",
      "/observability": "timeline",
      "/usage": "usage-aggregates",
    };

    for (const [path, resource] of Object.entries(expected)) {
      expect(navigation.find((item) => item.path === path)?.apiResource).toBe(resource);
    }
  });

  it("preserves meaningful navigation context on stable deep links", () => {
    expect(navigationItemForPath("/projects/project_1")?.path).toBe("/projects");
    expect(navigationItemForPath("/workspaces/workspace_1")?.path).toBe("/projects");
    expect(navigationItemForPath("/tasks/task_1")?.path).toBe("/tasks");
    expect(navigationItemForPath("/results/result_1")?.path).toBe("/files");
    expect(navigationItemForPath("/artifacts/artifact_1")?.path).toBe("/files");
    expect(navigationItemForPath("/workflows/workflow_1")?.path).toBe("/templates");
    expect(navigationItemForPath("/models/providers/provider_1")?.path).toBe("/models");
    expect(navigationItemForPath("/import-export/previews/preview_1")?.path).toBe("/import-export");
    expect(navigationItemForPath("/unknown/thing")).toBeUndefined();
  });

  it("marks the Experimental Learning surface contextually", () => {
    expect(navigationMaturity["/learning"]).toBe("experimental");
    expect(navigationMaturity["/tasks"]).toBeUndefined();
  });

  it("keeps Marketplace availability manifest-gated and HA routes out of the baseline shell", () => {
    const marketplace = navigation.find((item) => item.path === "/marketplace");
    const paths = navigation.map((item) => item.path);
    expect(marketplace?.apiResource).toBe("registry-items");
    expect(paths.some((path) => path.includes("failover") || path.includes("/ha"))).toBe(false);
  });
});
