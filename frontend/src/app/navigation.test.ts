import { describe, expect, it } from "vitest";
import { navigation } from "./navigation";

describe("#17 stable navigation baseline", () => {
  it("keeps the major canonical product routes stable", () => {
    const paths = new Set(navigation.map((item) => item.path));
    for (const path of [
      "/onboarding",
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
      "/marketplace",
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
    const onboarding = navigation.find((item) => item.path === "/onboarding");
    const tools = navigation.find((item) => item.path === "/tools");
    const integrations = navigation.find((item) => item.path === "/integrations");
    const evaluations = navigation.find((item) => item.path === "/evaluations");
    const marketplace = navigation.find((item) => item.path === "/marketplace");
    const compute = navigation.find((item) => item.path === "/compute");
    const plugins = navigation.find((item) => item.path === "/plugins");

    expect(onboarding?.apiResource).toBe("onboarding");
    expect(tools?.apiResource).toBe("capabilities");
    expect(integrations?.apiResource).toBe("connector-definitions");
    expect(evaluations?.apiResource).toBe("evaluation-suites");
    expect(marketplace?.apiResource).toBe("registry-items");
    expect(compute?.apiResource).toBe("nodes");
    expect(plugins?.apiResource).toBe("plugins");
  });

  it("keeps Marketplace availability manifest-gated and HA routes out of the baseline shell", () => {
    const marketplace = navigation.find((item) => item.path === "/marketplace");
    const paths = navigation.map((item) => item.path);
    expect(marketplace?.apiResource).toBe("registry-items");
    expect(paths.some((path) => path.includes("failover") || path.includes("/ha"))).toBe(false);
  });
});
