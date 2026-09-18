import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RegistryClient, type RegistryItem, type RegistryKindDescriptor } from "../api/registry";
import { MarketplacePage, marketplacePresentation } from "./MarketplacePage";

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

describe("MarketplacePage", () => {
  it("renders the unified Marketplace as the default product surface", () => {
    const client = new RegistryClient({ fetchImpl: vi.fn() });
    const html = renderToStaticMarkup(<MarketplacePage client={client} />);

    expect(html).toContain("Marketplace");
    expect(html).toContain("Unified component catalog");
    expect(html).toContain("Component kinds");
    expect(html).toContain(">All<");
    expect(html).toContain("Tools");
    expect(html).toContain("Skills");
    expect(html).toContain("Plugins");
    expect(html).toContain("Connectors");
    expect(html).toContain("Applications");
    expect(html).toContain("Templates / Workflows");
    expect(html).toContain("Component kind");
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
  });

  it("keeps future kinds renderable without a frontend enum change", () => {
    const future: RegistryKindDescriptor = {
      kind: "notebook_extension",
      display_name: "Notebook Extension",
      default_route: "kind_handler",
      supports_install: true,
      supports_update: true,
      supports_uninstall: true,
    };

    const application: RegistryKindDescriptor = {
      kind: "application",
      display_name: "Application",
      default_route: "kind_handler",
      supports_install: true,
      supports_update: false,
      supports_uninstall: true,
    };
    const merged = marketplacePresentation.mergeKindDescriptors(
      [future, application],
      [item({ item_type: "research_surface", route: "kind_handler" })],
    );

    expect(merged.find((entry) => entry.kind === "notebook_extension")?.display_name).toBe(
      "Notebook Extension",
    );
    expect(merged.find((entry) => entry.kind === "research_surface")?.display_name).toBe(
      "Research Surface",
    );
    expect(
      merged.find((entry) => entry.kind === "application")?.management_path,
    ).toBe("/applications");
    expect(marketplacePresentation.humanizeKind("future-kind")).toBe("Future Kind");
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
    expect(marketplacePresentation.operationSupported(sourceSwitch, "update")).toBe(true);
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
  it("uses Control Plane route and owner capability metadata for mutation support", () => {
    const installable = item({ route_available: true });
    expect(marketplacePresentation.operationSupported(installable, "install")).toBe(true);

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
    expect(marketplacePresentation.operationSupported(missingHandler, "install")).toBe(false);
    expect(marketplacePresentation.itemStateLabel(missingHandler)).toBe("missing handler");

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
      },
    });
    expect(marketplacePresentation.uninstallSupported(uninstallable)).toBe(true);
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

    expect(marketplacePresentation.uninstallSupported(installedFromOfficial)).toBe(true);
    expect(marketplacePresentation.uninstallSupported(privateCandidate)).toBe(false);
    expect(marketplacePresentation.mutationOperation(privateCandidate)).toBe("update");
    expect(marketplacePresentation.itemStateLabel(privateCandidate)).toBe("source change available");
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
    expect(marketplacePresentation.mutationOperation(applicationUpdate)).toBe("update");
    expect(marketplacePresentation.operationSupported(applicationUpdate, "update")).toBe(false);
    expect(marketplacePresentation.uninstallSupported(applicationUpdate)).toBe(true);
  });

});
