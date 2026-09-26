import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlPlaneError } from "../api/client";
import { RegistryClient, type RegistryItem, type RegistryKindDescriptor } from "../api/registry";
import { matchPath } from "../app/router";
import { MARKETPLACE_ITEM_ROUTE, MarketplacePage, marketplacePresentation } from "./MarketplacePage";

function item(overrides: Partial<RegistryItem> = {}): RegistryItem {
  return {
    id: "example.tool@1.0.0",
    type: "registry-item",
    item_id: "example.tool",
    item_type: "tool",
    name: "Example tool",
    description: "Example",
    version: "1.0.0",
    publisher: "example",
    source: {
      repository: "https://example.invalid/tool",
      package_reference: "example/tool@1.0.0",
      revision: null,
    },
    license: "MIT",
    provenance: "test",
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
    trust_status: "reviewed",
    review_reference: null,
    released_at: null,
    changelog: null,
    deprecated: false,
    yanked: false,
    route: "portable_import",
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

function kindDescriptor(
  overrides: Partial<RegistryKindDescriptor> = {},
): RegistryKindDescriptor {
  return {
    kind: "tool",
    display_name: "Tool",
    default_route: "portable_import",
    supports_install: true,
    supports_update: true,
    supports_uninstall: true,
    group: "tools_integrations",
    management_path: null,
    ...overrides,
  };
}

describe("MarketplacePage", () => {
  it("renders the unified Marketplace as the default product surface", () => {
    const client = new RegistryClient({ fetchImpl: vi.fn() });
    const html = renderToStaticMarkup(<MarketplacePage client={client} />);

    expect(html).toContain("Marketplace");
    expect(html).toContain("Unified component catalog");
    expect(html).toContain("Component kinds");
    expect(html).toContain(">All<");
    expect(html).toContain("AI &amp; Agents");
    expect(html).toContain("Models");
    expect(html).toContain("Tools &amp; Integrations");
    expect(html).toContain("Applications");
    expect(html).toContain("Platform Extensions");
    expect(html).toContain("Content");
    expect(html).toContain("Component kind");
    expect(html).toContain("<details><summary>Filters</summary>");
    expect(html).not.toContain("<details open");
    expect(html).toContain("All trust states");
    expect(html).toContain("All maturity levels");
    expect(html).toContain("Marketplace source");
    expect(html).toContain("Installed state");
    expect(html).toContain("Compatibility");
    expect(html).toContain("Deprecated state");
    expect(html).toContain("Yanked state");
    expect(html).toContain("Technical components only");
    expect(html).toContain("Updates only");
    expect(html).toContain("Sort");
    expect(html).toContain("Direction");
    expect(html).toContain("Loading Marketplace");
    expect(html).toContain("Refresh Marketplace");
  });

  it("keeps every integrated V1 Marketplace kind addressable through a stable deep link", () => {
    const kinds = [
      "agent",
      "agent_team",
      "orchestrator",
      "executor",
      "model_provider",
      "capability_provider",
      "memory_provider",
      "file_provider",
      "knowledge_provider",
      "observability_exporter",
      "automation_provider",
      "evaluator",
      "tool",
      "skill",
      "plugin",
      "workflow",
      "template",
      "model_configuration",
      "connector",
      "application",
      "evaluation",
      "documentation",
    ];

    for (const kind of kinds) {
      const candidate = item({
        id: `${kind}.example@1.0.0`,
        qualified_id: `official::${kind}.example@1.0.0`,
        item_id: `${kind}.example`,
        item_type: kind,
        source_registry: "official",
      });
      const href = marketplacePresentation.marketplaceItemHref(candidate);
      const match = matchPath(MARKETPLACE_ITEM_ROUTE, href);
      expect(match?.resourceId, `missing deep link for ${kind}`).toBe(
        `official::${kind}.example@1.0.0`,
      );
    }
  });

  it("falls back to a deterministic source-qualified Marketplace identity", () => {
    const candidate = item({
      qualified_id: undefined,
      item_id: "example.agent",
      item_type: "agent",
      version: "2.0.0",
      source_registry: "private",
    });

    expect(marketplacePresentation.marketplaceItemResourceId(candidate)).toBe(
      "private::example.agent@2.0.0",
    );
    expect(marketplacePresentation.marketplaceItemHref(candidate)).toBe(
      "/marketplace/items/private%3A%3Aexample.agent%402.0.0",
    );
  });

  it("keeps semantic Marketplace kinds separate from their technical packaging", () => {
    const merged = marketplacePresentation.mergeKindDescriptors(
      [
        kindDescriptor({
          kind: "orchestrator",
          display_name: "Orchestrator",
          default_route: "kind_handler",
          group: "ai_agents",
          management_path: "/plugins",
        }),
        kindDescriptor({
          kind: "model_provider",
          display_name: "Model Provider",
          default_route: "kind_handler",
          group: "models",
          management_path: "/models",
        }),
        kindDescriptor({
          kind: "agent",
          display_name: "Agent",
          default_route: "kind_handler",
          group: "ai_agents",
          management_path: "/agents",
        }),
      ],
      [
        item({ item_type: "orchestrator", route: "kind_handler" }),
        item({ item_type: "model_provider", route: "kind_handler" }),
        item({ item_type: "agent", route: "kind_handler" }),
      ],
    );

    expect(merged.find((entry) => entry.kind === "orchestrator")?.management_path).toBe("/plugins");
    expect(merged.find((entry) => entry.kind === "orchestrator")?.group).toBe("ai_agents");
    expect(merged.find((entry) => entry.kind === "model_provider")?.management_path).toBe("/models");
    expect(merged.find((entry) => entry.kind === "model_provider")?.group).toBe("models");
    expect(merged.find((entry) => entry.kind === "agent")?.management_path).toBe("/agents");
  });

  it("keeps future kinds renderable without a frontend enum change", () => {
    const future: RegistryKindDescriptor = {
      kind: "notebook_extension",
      display_name: "Notebook Extension",
      default_route: "kind_handler",
      supports_install: true,
      supports_update: true,
      supports_uninstall: true,
      group: "content",
    };

    const application: RegistryKindDescriptor = {
      kind: "application",
      display_name: "Application",
      default_route: "kind_handler",
      supports_install: true,
      supports_update: false,
      supports_uninstall: true,
      management_path: "/applications",
    };
    const merged = marketplacePresentation.mergeKindDescriptors(
      [future, application],
      [item({ item_type: "research_surface", route: "kind_handler" })],
    );

    expect(merged.find((entry) => entry.kind === "notebook_extension")?.display_name).toBe(
      "Notebook Extension",
    );
    expect(merged.find((entry) => entry.kind === "notebook_extension")?.group).toBe("content");
    expect(merged.find((entry) => entry.kind === "research_surface")?.display_name).toBe(
      "Research Surface",
    );
    expect(
      merged.find((entry) => entry.kind === "application")?.management_path,
    ).toBe("/applications");
    expect(marketplacePresentation.humanizeKind("future-kind")).toBe("Future Kind");
    const unknown = merged.find((entry) => entry.kind === "research_surface");
    expect(unknown?.supports_install).toBe(false);
    expect(unknown?.supports_update).toBe(false);
    expect(unknown?.supports_uninstall).toBe(false);
    expect(unknown?.management_path).toBeNull();
  });

  it("loads every canonical Marketplace kind descriptor page before deriving lifecycle support", async () => {
    const client = new RegistryClient({ fetchImpl: vi.fn() });
    const first = kindDescriptor({
      kind: "kind_001",
      display_name: "Kind 001",
      supports_install: false,
      supports_update: false,
      supports_uninstall: false,
    });
    const later = kindDescriptor({
      kind: "kind_201",
      display_name: "Kind 201",
      supports_install: true,
      supports_update: true,
      supports_uninstall: true,
    });
    const listKinds = vi
      .spyOn(client, "listKinds")
      .mockResolvedValueOnce({
        items: [first],
        next_cursor: "page-2",
        total: 201,
        limit: 200,
      })
      .mockResolvedValueOnce({
        items: [later],
        next_cursor: null,
        total: 201,
        limit: 200,
      });

    const descriptors = await marketplacePresentation.listAllKindDescriptors(client);

    expect(descriptors.map((entry) => entry.kind)).toEqual(["kind_001", "kind_201"]);
    expect(listKinds).toHaveBeenCalledTimes(2);
    expect(listKinds).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ cursor: "page-2", limit: 200 }),
    );
    expect(descriptors[1]?.supports_install).toBe(true);
  });

  it("distinguishes disabled Marketplace providers from permission and backend failures", () => {
    expect(marketplacePresentation.providerLooksDisabled(new Error("404 not found"))).toBe(true);
    expect(marketplacePresentation.providerLooksDisabled(new Error("provider disabled"))).toBe(true);
    expect(marketplacePresentation.providerLooksDisabled(new Error("403 permission denied"))).toBe(false);
    expect(marketplacePresentation.providerLooksDisabled(new Error("backend offline"))).toBe(false);

    const denied = new ControlPlaneError(403, {
      code: "forbidden",
      category: "authorization",
      message: "operation denied",
      request_id: "request_marketplace",
      correlation_id: "correlation_marketplace",
      retryable: false,
    });
    expect(marketplacePresentation.isMarketplaceAccessFailure(denied)).toBe(true);
    expect(marketplacePresentation.isMarketplaceAccessFailure(new Error("backend offline"))).toBe(false);
  });

  it("derives install and update verbs from canonical installed state", () => {
    expect(marketplacePresentation.mutationOperation(item())).toBe("install");
    expect(
      marketplacePresentation.mutationOperation(
        item({
          installed: true,
          installed_version: "1.0.0",
          version: "1.1.0",
          update_available: true,
        }),
      ),
    ).toBe("update");
    expect(
      marketplacePresentation.mutationOperation(
        item({ installed: true, installed_version: "1.0.0", update_available: false }),
      ),
    ).toBeNull();
  });

  it("treats a same-version candidate from another source as an explicit update", () => {
    const sourceSwitch = item({
      installed: true,
      installed_version: "1.0.0",
      installed_source_registry: "official",
      installation_source_matches: false,
      source_registry: "private",
      version: "1.0.0",
      route: "kind_handler",
      route_available: true,
      owner_extension: {
        handler_available: true,
        requirements: null,
        details: null,
        status: null,
        supported_operations: ["install", "update", "uninstall"],
      },
    });

    expect(marketplacePresentation.mutationOperation(sourceSwitch)).toBe("update");
    expect(
      marketplacePresentation.operationSupported(
        sourceSwitch,
        "update",
        kindDescriptor({
          kind: "skill",
          display_name: "Skill",
          default_route: "kind_handler",
        }),
      ),
    ).toBe(true);
    expect(marketplacePresentation.itemStateLabel(sourceSwitch)).toBe("source change available");
  });

  it("presents manual, pinned-update and blocked states distinctly", () => {
    expect(marketplacePresentation.itemStateLabel(item({ route: "manual" }))).toBe("manual");
    expect(
      marketplacePresentation.itemStateLabel(
        item({
          installed: true,
          installed_version: "1.0.0",
          version: "1.1.0",
          update_available: true,
          pinned_version: "1.0.0",
        }),
      ),
    ).toBe("update available · pinned");
    expect(marketplacePresentation.itemStateLabel(item({ operation_state: "blocked" }))).toBe(
      "blocked",
    );
  });
  it("uses only canonical kind and owner capability metadata for mutation support", () => {
    const toolKind = kindDescriptor();
    const installable = item({ route_available: true });
    expect(
      marketplacePresentation.operationSupported(installable, "install", toolKind),
    ).toBe(true);
    expect(
      marketplacePresentation.operationSupported(installable, "install", undefined),
    ).toBe(false);

    const missingHandler = item({
      item_type: "skill",
      route: "kind_handler",
      route_available: false,
      owner_extension: {
        handler_available: false,
        requirements: null,
        details: null,
        status: null,
      },
    });
    const skillKind = kindDescriptor({
      kind: "skill",
      display_name: "Skill",
      default_route: "kind_handler",
    });
    expect(
      marketplacePresentation.operationSupported(missingHandler, "install", skillKind),
    ).toBe(false);
    expect(marketplacePresentation.itemStateLabel(missingHandler)).toBe("missing handler");

    const missingOwnerOperations = item({
      item_type: "notebook_extension",
      route: "kind_handler",
      route_available: true,
      owner_extension: {
        handler_available: true,
        requirements: null,
        details: null,
        status: null,
      },
    });
    const futureKind = kindDescriptor({
      kind: "notebook_extension",
      display_name: "Notebook Extension",
      default_route: "kind_handler",
    });
    expect(
      marketplacePresentation.operationSupported(
        missingOwnerOperations,
        "install",
        futureKind,
      ),
    ).toBe(false);

    const uninstallable = item({
      item_type: "notebook_extension",
      route: "kind_handler",
      route_available: true,
      installed: true,
      installed_version: "1.0.0",
      owner_extension: {
        handler_available: true,
        requirements: null,
        details: null,
        status: null,
        supported_operations: ["install", "update", "uninstall"],
      },
    });
    expect(
      marketplacePresentation.uninstallSupported(uninstallable, futureKind),
    ).toBe(true);
    expect(
      marketplacePresentation.uninstallSupported(
        item({
          item_type: "plugin",
          route: "plugin",
          route_available: true,
          installed: true,
          installed_version: "1.0.0",
          owner_extension: {
            handler_available: true,
            requirements: null,
            details: null,
            status: null,
            supported_operations: ["install", "update", "uninstall"],
          },
        }),
        kindDescriptor({
          kind: "plugin",
          display_name: "Plugin",
          default_route: "plugin",
        }),
      ),
    ).toBe(true);
  });

  it("scopes installed owner actions to the exact Marketplace source", () => {
    const installedFromOfficial = item({
      item_type: "skill",
      route: "kind_handler",
      route_available: true,
      source_registry: "official",
      installed: true,
      installed_version: "1.0.0",
      installed_source_registry: "official",
      installation_source_matches: true,
      owner_extension: {
        handler_available: true,
        requirements: null,
        details: null,
        status: null,
        supported_operations: ["install", "update", "uninstall"],
      },
    });
    const privateCandidate = {
      ...installedFromOfficial,
      source_registry: "private",
      installation_source_matches: false,
    };

    const skillKind = kindDescriptor({
      kind: "skill",
      display_name: "Skill",
      default_route: "kind_handler",
    });
    expect(
      marketplacePresentation.uninstallSupported(installedFromOfficial, skillKind),
    ).toBe(true);
    expect(
      marketplacePresentation.uninstallSupported(privateCandidate, skillKind),
    ).toBe(false);
    expect(marketplacePresentation.mutationOperation(privateCandidate)).toBe("update");
    expect(marketplacePresentation.itemStateLabel(privateCandidate)).toBe("source change available");
  });

  it("renders canonical unsupported state without inventing a fallback capability", () => {
    const unsupported = item({
      item_type: "agent",
      route: "kind_handler",
      route_available: false,
      operation_state: "unsupported",
      deprecated: true,
      owner_extension: {
        handler_available: true,
        requirements: null,
        details: null,
        status: { state: "deprecated" },
        supported_operations: [],
      },
    });
    const agentKind = kindDescriptor({
      kind: "agent",
      display_name: "Agent",
      default_route: "kind_handler",
    });

    expect(marketplacePresentation.itemStateLabel(unsupported)).toBe("unsupported");
    expect(
      marketplacePresentation.operationSupported(unsupported, "install", agentKind),
    ).toBe(false);
    expect(
      marketplacePresentation.uninstallSupported(unsupported, agentKind),
    ).toBe(false);
  });

  it("fails closed for owner operations that are intentionally unsupported", () => {
    const applicationUpdate = item({
      item_type: "application",
      route: "kind_handler",
      route_available: true,
      installed: true,
      installed_version: "1.0.0",
      version: "1.1.0",
      update_available: true,
      owner_extension: {
        handler_available: true,
        requirements: null,
        details: null,
        status: null,
        supported_operations: ["install", "uninstall"],
      },
    });
    const applicationKind = kindDescriptor({
      kind: "application",
      display_name: "Application",
      default_route: "kind_handler",
      supports_update: false,
      management_path: "/applications",
    });
    expect(marketplacePresentation.mutationOperation(applicationUpdate)).toBe("update");
    expect(
      marketplacePresentation.operationSupported(
        applicationUpdate,
        "update",
        applicationKind,
      ),
    ).toBe(false);
    expect(
      marketplacePresentation.uninstallSupported(applicationUpdate, applicationKind),
    ).toBe(true);
  });

});
