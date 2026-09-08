import { describe, expect, it } from "vitest";
import type { RegistryItem } from "../api/registry";
import { isTechnicalComponent, technicalCategoryLabel, technicalMetadata } from "./technical";

function registryItem(overrides: Partial<RegistryItem> = {}): RegistryItem {
  return {
    id: "candidate@1.0.0",
    type: "registry-item",
    item_id: "candidate",
    item_type: "tool",
    name: "Candidate",
    description: "Candidate fixture",
    version: "1.0.0",
    publisher: "example",
    source: {
      repository: "https://example.invalid/candidate",
      package_reference: "github:example/candidate",
      revision: null,
    },
    license: "MIT",
    provenance: "test fixture",
    minimum_platform_version: null,
    maximum_platform_version: null,
    dependencies: [],
    requested_permissions: [],
    required_capabilities: [],
    required_plugins: [],
    required_connectors: [],
    required_models: [],
    tags: [],
    categories: [],
    trust_status: "untrusted",
    review_reference: null,
    released_at: null,
    changelog: null,
    deprecated: false,
    yanked: false,
    route: "manual",
    integrity: {
      sha256: null,
      signature_present: false,
      signature_key_id: null,
    },
    installed: false,
    installed_version: null,
    pinned_version: null,
    update_available: false,
    installation: null,
    ...overrides,
  };
}

describe("technical Marketplace metadata", () => {
  it("derives lifecycle, evaluation, deployment, cost and references from canonical tags", () => {
    const item = registryItem({
      categories: ["code-intelligence"],
      tags: [
        "lifecycle:candidate",
        "evaluation:required",
        "deployment:local",
        "deployment:cli",
        "cost:compatible",
        "network:optional",
        "provider:llm",
        "alternative:other-tool",
        "resource:medium",
        "architecture-ref:docs/architecture.md",
        "evaluation-ref:eval-123",
      ],
    });

    expect(isTechnicalComponent(item)).toBe(true);
    expect(technicalMetadata(item)).toEqual({
      categories: ["code-intelligence"],
      lifecycle: "candidate",
      evaluation: "required",
      deploymentModes: ["cli", "local"],
      costStatus: "compatible",
      networkStatus: "optional",
      providerRequirements: ["llm"],
      alternatives: ["other-tool"],
      resourceClass: "medium",
      architectureReference: "docs/architecture.md",
      decisionReference: null,
      evaluationReference: "eval-123",
    });
    expect(technicalCategoryLabel("code-intelligence")).toBe("Code intelligence");
  });

  it("keeps missing technical facts explicit", () => {
    const metadata = technicalMetadata(registryItem({ categories: ["evaluation"] }));

    expect(metadata).not.toBeNull();
    expect(metadata?.lifecycle).toBe("unknown");
    expect(metadata?.evaluation).toBe("unknown");
    expect(metadata?.deploymentModes).toEqual(["unknown"]);
    expect(metadata?.costStatus).toBe("unknown");
    expect(metadata?.networkStatus).toBe("unknown");
    expect(metadata?.resourceClass).toBeNull();
  });

  it("does not classify ordinary Registry assets as technical components", () => {
    const item = registryItem({ item_type: "connector", categories: ["productivity"] });

    expect(isTechnicalComponent(item)).toBe(false);
    expect(technicalMetadata(item)).toBeNull();
  });
});
