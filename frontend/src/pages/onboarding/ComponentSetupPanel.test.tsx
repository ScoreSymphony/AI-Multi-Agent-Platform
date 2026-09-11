import { describe, expect, it } from "vitest";
import type {
  ComponentAvailability,
  ComponentCategory,
  ComponentCompatibilityState,
  ComponentLifecycle,
  ComponentSetupMode,
  DiscoveredComponent,
} from "../../api/onboarding";
import { recommendComponentDefaults } from "./ComponentSetupPanel";

function component({
  id,
  category = "model_provider",
  lifecycle = "supported",
  compatibility = "compatible",
  modes = [],
  priority = 0,
}: {
  id: string;
  category?: ComponentCategory;
  lifecycle?: ComponentLifecycle;
  compatibility?: ComponentCompatibilityState;
  modes?: ComponentSetupMode[];
  priority?: number;
}): DiscoveredComponent {
  const availability: ComponentAvailability = compatibility === "installation_required"
    ? "installation_required"
    : compatibility === "unavailable"
      ? "unavailable"
      : "available";
  return {
    component_id: id,
    category,
    display_name: id,
    availability,
    lifecycle,
    version: null,
    capabilities: [],
    requirements: [],
    recommended_modes: modes,
    priority,
    source_ref: null,
    metadata: {},
    compatibility: {
      component_id: id,
      category,
      state: compatibility,
      reasons: [],
      missing_requirements: [],
    },
  };
}

describe("component setup recommendation preview", () => {
  it("matches backend ordering by lifecycle, priority and stable component id", () => {
    const components = [
      component({ id: "supported-high", lifecycle: "supported", priority: 100 }),
      component({ id: "recommended-low", lifecycle: "recommended", priority: 1 }),
      component({ id: "recommended-z", lifecycle: "recommended", priority: 10 }),
      component({ id: "recommended-a", lifecycle: "recommended", priority: 10 }),
    ];

    expect(recommendComponentDefaults(components, "auto")).toEqual({
      model_provider: "recommended-a",
    });
  });

  it("respects setup-mode recommendations without making one provider mandatory", () => {
    const components = [
      component({ id: "local-model", lifecycle: "recommended", modes: ["local"], priority: 10 }),
      component({ id: "multi-node-model", lifecycle: "recommended", modes: ["multi_node"], priority: 20 }),
      component({ id: "portable-model", lifecycle: "supported", modes: [], priority: 5 }),
    ];

    expect(recommendComponentDefaults(components, "local")).toEqual({ model_provider: "local-model" });
    expect(recommendComponentDefaults(components, "multi_node")).toEqual({ model_provider: "multi-node-model" });
    expect(recommendComponentDefaults(components, "auto")).toEqual({ model_provider: "portable-model" });
  });

  it("never auto-selects experimental, unavailable or insufficient-hardware components", () => {
    const components = [
      component({ id: "experimental", lifecycle: "experimental", priority: 100 }),
      component({ id: "missing-runtime", lifecycle: "recommended", compatibility: "installation_required", priority: 90 }),
      component({ id: "missing-hardware", lifecycle: "recommended", compatibility: "insufficient_hardware", priority: 80 }),
      component({ id: "healthy", lifecycle: "supported", compatibility: "compatible_with_constraints", priority: 1 }),
    ];

    expect(recommendComponentDefaults(components, "auto")).toEqual({ model_provider: "healthy" });
  });

  it("selects defaults independently for every justified provider category", () => {
    const components = [
      component({ id: "orchestrator-native", category: "orchestrator", lifecycle: "recommended" }),
      component({ id: "model-local", category: "model_provider", lifecycle: "recommended" }),
      component({ id: "executor-reference", category: "executor", lifecycle: "recommended" }),
      component({ id: "storage-local", category: "storage", lifecycle: "recommended" }),
    ];

    expect(recommendComponentDefaults(components, "auto")).toEqual({
      orchestrator: "orchestrator-native",
      model_provider: "model-local",
      executor: "executor-reference",
      storage: "storage-local",
    });
  });
});
