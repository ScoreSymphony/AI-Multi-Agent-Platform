import { describe, expect, it } from "vitest";
import { navigation } from "./navigation";

describe("#17 stable navigation baseline", () => {
  it("keeps the major canonical product routes stable", () => {
    const paths = new Set(navigation.map((item) => item.path));
    for (const path of [
      "/projects",
      "/tasks",
      "/runs",
      "/files",
      "/models",
      "/events",
      "/observability",
      "/usage",
    ]) {
      expect(paths.has(path)).toBe(true);
    }
  });

  it("does not expose optional Registry or HA navigation in the baseline shell", () => {
    const paths = navigation.map((item) => item.path);
    expect(paths.some((path) => path.includes("registry"))).toBe(false);
    expect(paths.some((path) => path.includes("failover") || path.includes("/ha"))).toBe(false);
  });
});
