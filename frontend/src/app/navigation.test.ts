import { describe, expect, it } from "vitest";
import { navigation } from "./navigation";

describe("#17 stable navigation baseline", () => {
  it("keeps the major canonical product routes stable", () => {
    const paths = new Set(navigation.map((item) => item.path));
    for (const path of [
      "/projects",
      "/tasks",
      "/runs",
      "/agents",
      "/agent-teams",
      "/files",
      "/search",
      "/tools",
      "/integrations",
      "/models",
      "/evaluations",
      "/compute",
      "/terminal",
      "/plugins",
      "/events",
      "/observability",
      "/usage",
    ]) {
      expect(paths.has(path)).toBe(true);
    }
  });

  it("binds implemented product routes to canonical collections", () => {
    const tools = navigation.find((item) => item.path === "/tools");
    const integrations = navigation.find((item) => item.path === "/integrations");
    const evaluations = navigation.find((item) => item.path === "/evaluations");
    const compute = navigation.find((item) => item.path === "/compute");
    const plugins = navigation.find((item) => item.path === "/plugins");

    expect(tools?.apiResource).toBe("capabilities");
    expect(integrations?.apiResource).toBe("connector-definitions");
    expect(evaluations?.apiResource).toBe("evaluation-suites");
    expect(compute?.apiResource).toBe("nodes");
    expect(plugins?.apiResource).toBe("plugins");
  });

  it("does not expose optional Registry or HA navigation in the baseline shell", () => {
    const paths = navigation.map((item) => item.path);
    expect(paths.some((path) => path.includes("registry"))).toBe(false);
    expect(paths.some((path) => path.includes("failover") || path.includes("/ha"))).toBe(false);
  });
});
