import React from "react";
import { createRoot } from "react-dom/client";
import { RegistryClient } from "/src/api/registry.ts";
import { MarketplacePage } from "/src/pages/MarketplacePage.tsx";

const calls = [];
window.__marketplaceCalls = calls;
const harnessParams = new URLSearchParams(window.location.search);
const providerMode = harnessParams.get("provider");
const mutationMode = harnessParams.get("mutation");

function errorResponse(status, code, message, retryable = false) {
  return new Response(
    JSON.stringify({
      code,
      category: "marketplace",
      message,
      request_id: "marketplace-browser-request",
      correlation_id: "marketplace-browser-correlation",
      retryable,
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function item({
  id,
  kind,
  name,
  description,
  route = "portable_import",
  installed = false,
  installedVersion = null,
  updateAvailable = false,
  pinnedVersion = null,
  permissions = [],
  dependencies = [],
  operationState = null,
  manifest = null,
}) {
  const handlerAvailable = route === "kind_handler";
  return {
    id: `${id}@1.1.0`,
    type: "registry-item",
    item_id: id,
    item_type: kind,
    kind,
    name,
    description,
    version: "1.1.0",
    publisher: "example",
    source: {
      repository: `https://example.invalid/${id}`,
      package_reference: `github:example/${id}`,
      revision: "abc123",
    },
    license: "MIT",
    provenance: "browser regression fixture",
    minimum_platform_version: null,
    maximum_platform_version: null,
    compatibility: {
      minimum_platform_version: null,
      maximum_platform_version: null,
      compatible: operationState !== "blocked",
      platform_compatible: operationState !== "blocked",
      operating_system_compatible: operationState !== "blocked",
      architecture_compatible: true,
      operating_systems: ["linux"],
      architectures: ["x86_64"],
      required_runtimes: ["node"],
      missing_runtimes: operationState === "blocked" ? ["runtime.gpu"] : [],
      missing_capabilities: [],
      missing_plugins: [],
      missing_connectors: [],
      missing_models: [],
    },
    dependencies,
    requested_permissions: permissions,
    required_capabilities: [],
    required_plugins: [],
    required_connectors: [],
    required_models: [],
    tags: [],
    categories: [],
    trust_status: "reviewed",
    trust: "reviewed",
    review_reference: "https://example.invalid/review",
    released_at: "2026-09-18T00:00:00Z",
    release_date: "2026-09-18T00:00:00Z",
    changelog: "Browser fixture release",
    deprecated: false,
    yanked: false,
    route,
    route_available: route !== "manual",
    manifest_reference: manifest,
    integrity: {
      sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      signature_present: true,
      signature_key_id: "browser-key",
    },
    installed,
    installed_version: installedVersion,
    pinned_version: pinnedVersion,
    update_available: updateAvailable,
    installation: installed
      ? {
          id,
          type: "registry-installation",
          item_id: id,
          version: installedVersion ?? "1.1.0",
          pinned_version: pinnedVersion,
          source_registry: "local",
          source_repository: `https://example.invalid/${id}`,
          package_reference: `github:example/${id}`,
          revision: "old123",
          license: "MIT",
          provenance: "browser regression fixture",
          history: [],
        }
      : null,
    update_state: {
      installed,
      installed_version: installedVersion,
      candidate_version: "1.1.0",
      pinned_version: pinnedVersion,
      update_available: updateAvailable,
    },
    owner_extension: handlerAvailable
      ? {
          handler_available: true,
          requirements: { runtime: "fixture-runtime" },
          details: kind === "application"
            ? { runtime: "container", definition: "application manifest" }
            : { owner: `${kind}-handler` },
          status: installed ? { state: "installed" } : { state: "available" },
          supported_operations:
            kind === "application"
              ? ["install", "uninstall"]
              : ["install", "update", "uninstall"],
        }
      : null,
    operation_state: operationState,
  };
}

const catalog = [
  item({
    id: "projectatlas",
    kind: "tool",
    name: "ProjectAtlas",
    description: "Repository intelligence tool",
  }),
  item({
    id: "code-review-skill",
    kind: "skill",
    name: "Code Review Skill",
    description: "Reusable review skill",
    route: "kind_handler",
  }),
  item({
    id: "example-plugin",
    kind: "plugin",
    name: "Example Plugin",
    description: "Installed plugin with an update",
    route: "plugin",
    installed: true,
    installedVersion: "1.0.0",
    updateAvailable: true,
    permissions: ["filesystem.read", "filesystem.write"],
    dependencies: [
      {
        item_id: "projectatlas",
        minimum_version: "1.0.0",
        maximum_version: null,
        optional: false,
        kind: "tool",
        status: "satisfied",
        installed_version: "1.1.0",
      },
    ],
  }),
  item({
    id: "github-connector",
    kind: "connector",
    name: "GitHub Connector",
    description: "Connector discovery fixture",
  }),
  item({
    id: "code-server",
    kind: "application",
    name: "Code Server",
    description: "Application definition fixture",
    route: "kind_handler",
    installed: true,
    installedVersion: "1.0.0",
    updateAvailable: true,
    manifest: {
      kind: "application",
      reference: "applications/code-server.yaml",
      schema_version: "3",
    },
  }),
  item({
    id: "release-template",
    kind: "template",
    name: "Release Template",
    description: "Reusable template",
  }),
  item({
    id: "release-workflow",
    kind: "workflow",
    name: "Release Workflow",
    description: "Reusable workflow",
  }),
  item({
    id: "notebook-extension",
    kind: "notebook_extension",
    name: "Notebook Extension",
    description: "Installed future Marketplace kind",
    route: "kind_handler",
    installed: true,
    installedVersion: "1.1.0",
  }),
  item({
    id: "blocked-tool",
    kind: "tool",
    name: "Blocked Tool",
    description: "Incompatible fixture",
    operationState: "blocked",
  }),
  item({
    id: "manual-reference",
    kind: "documentation",
    name: "Manual Reference",
    description: "Manual-only catalog item",
    route: "manual",
    operationState: "manual",
  }),
];

function listProjection(entry) {
  return {
    ...entry,
    owner_extension: null,
  };
}

function pageFor(items, cursor) {
  const pageSize = 5;
  const projected = items.map(listProjection);
  if (cursor === "page-2") {
    return { items: projected.slice(pageSize), next_cursor: null, total: projected.length, limit: pageSize };
  }
  return {
    items: projected.slice(0, pageSize),
    next_cursor: projected.length > pageSize ? "page-2" : null,
    total: projected.length,
    limit: pageSize,
  };
}

function findItem(resourceRef) {
  return catalog.find((entry) => entry.item_id === resourceRef);
}

function syncInstallation(found) {
  found.installation = found.installed
    ? {
        id: found.item_id,
        type: "registry-installation",
        item_id: found.item_id,
        version: found.installed_version ?? found.version,
        pinned_version: found.pinned_version,
        source_registry: "local",
        source_repository: found.source.repository,
        package_reference: found.source.package_reference,
        revision: found.source.revision,
        license: found.license,
        provenance: found.provenance,
        history: [],
      }
    : null;
  found.update_state = {
    installed: found.installed,
    installed_version: found.installed_version,
    candidate_version: found.version,
    pinned_version: found.pinned_version,
    update_available: found.update_available,
  };
  if (found.owner_extension) {
    found.owner_extension = {
      ...found.owner_extension,
      status: { state: found.installed ? "installed" : "available" },
    };
  }
}

function marketplaceDecision(found, blocked) {
  const isPluginUpdate = found.item_id === "example-plugin";
  return {
    operation: found.installed ? "update" : "install",
    dependencies: found.dependencies.map((dependency) => ({
      required_by: found.item_id,
      item_id: dependency.item_id,
      item_kind: dependency.kind ?? null,
      optional: dependency.optional,
      minimum_version: dependency.minimum_version,
      maximum_version: dependency.maximum_version,
      status: dependency.status ?? "satisfied",
      installed_version: dependency.installed_version ?? null,
      candidate_version: dependency.installed_version ?? dependency.minimum_version ?? null,
      candidate_kind: dependency.kind ?? null,
      candidate_source_registry: "local",
      path: [found.item_id, dependency.item_id],
      blocking: dependency.status !== "satisfied" && !dependency.optional,
    })),
    compatibility: {
      compatible: !blocked,
      platform_compatible: !blocked,
      operating_system_compatible: !blocked,
      architecture_compatible: true,
      missing_runtimes: blocked ? ["runtime.gpu"] : [],
      missing_capabilities: [],
      missing_plugins: [],
      missing_connectors: [],
      missing_models: [],
    },
    permission_diff: {
      previous: isPluginUpdate ? ["filesystem.read"] : [],
      requested: isPluginUpdate ? ["filesystem.read", "filesystem.write"] : found.requested_permissions,
      added: isPluginUpdate ? ["filesystem.write"] : [],
      removed: [],
      unchanged: isPluginUpdate ? ["filesystem.read"] : [],
      changed: isPluginUpdate,
      escalated: isPluginUpdate,
    },
    provenance_diff: {
      installed: found.installed,
      previous_source_registry: isPluginUpdate ? "legacy-registry" : null,
      candidate_source_registry: "local",
      previous_publisher: isPluginUpdate ? "legacy-publisher" : null,
      candidate_publisher: found.publisher,
      previous_repository: found.installed ? "https://example.invalid/legacy-repo" : null,
      candidate_repository: found.source.repository,
      previous_package_reference: found.installed ? "legacy/package@1.0.0" : null,
      candidate_package_reference: found.source.package_reference,
      previous_revision: found.installed ? "old123" : null,
      candidate_revision: found.source.revision,
      previous_artifact_sha256: found.installed ? "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" : null,
      candidate_artifact_sha256: found.integrity.sha256,
      previous_signature_key_id: found.installed ? "old-key" : null,
      candidate_signature_key_id: found.integrity.signature_key_id,
      previous_trust_status: found.installed ? "trusted" : null,
      candidate_trust_status: found.trust_status,
      previous_review_reference: found.installed ? "https://example.invalid/old-review" : null,
      candidate_review_reference: found.review_reference,
      source_changed: isPluginUpdate,
      publisher_changed: isPluginUpdate,
      repository_changed: found.installed,
      package_reference_changed: found.installed,
      revision_changed: found.installed,
      artifact_changed: found.installed,
      signature_changed: found.installed,
      signature_key_changed: found.installed,
      trust_changed: false,
      trust_downgraded: false,
      review_changed: found.installed,
      changed: found.installed || isPluginUpdate,
    },
    approval: {
      required: isPluginUpdate,
      reasons: isPluginUpdate ? ["Permission escalation requires approval"] : [],
      authorization_required: true,
    },
    update_state: {
      installed_version: found.installed_version,
      candidate_version: found.version,
      latest_compatible_version: blocked ? null : found.version,
      update_available: found.update_available,
      pinned: Boolean(found.pinned_version),
      blocked_by_pin: Boolean(found.pinned_version && found.update_available),
      incompatible_update: blocked,
      current_yanked: false,
      candidate_yanked: found.yanked,
      candidate_deprecated: found.deprecated,
      source_change: isPluginUpdate,
      permission_change: isPluginUpdate,
      trust_integrity_issue: isPluginUpdate,
    },
    dependency_blocked: false,
  };
}

const fetchImpl = async (input, init = {}) => {
  const url = String(input);
  const parsed = new URL(url, window.location.origin);
  const body = init.body ? JSON.parse(String(init.body)) : null;
  calls.push({ url, method: init.method ?? "GET", body });

  if (parsed.pathname === "/api/v1/registry-items") {
    if (providerMode === "disabled") {
      return errorResponse(404, "not_found", "Marketplace provider is not configured");
    }
    if (providerMode === "unavailable") {
      return errorResponse(503, "unavailable", "Marketplace provider unavailable", true);
    }
    let items = [...catalog];
    const q = parsed.searchParams.get("q")?.toLowerCase();
    const kindFilter = parsed.searchParams.get("filter[item_type]");
    const updatesOnly = parsed.searchParams.get("filter[update_available]") === "true";
    const installed = parsed.searchParams.get("filter[installed]");
    const compatible = parsed.searchParams.get("filter[compatible]");
    if (q) {
      items = items.filter((entry) =>
        [entry.name, entry.description, entry.publisher, entry.item_id]
          .some((value) => value.toLowerCase().includes(q)),
      );
    }
    if (kindFilter) {
      const accepted = new Set(kindFilter.split(","));
      items = items.filter((entry) => accepted.has(entry.item_type));
    }
    if (updatesOnly) items = items.filter((entry) => entry.update_available);
    if (installed === "true") items = items.filter((entry) => entry.installed);
    if (installed === "false") items = items.filter((entry) => !entry.installed);
    if (compatible === "true") {
      items = items.filter((entry) => entry.compatibility.platform_compatible === true);
    }
    if (compatible === "false") {
      items = items.filter((entry) => entry.compatibility.platform_compatible === false);
    }
    return new Response(
      JSON.stringify(pageFor(items, parsed.searchParams.get("cursor"))),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }

  if (parsed.pathname.startsWith("/api/v1/registry-items/")) {
    const resourceId = decodeURIComponent(parsed.pathname.split("/").pop() ?? "");
    const itemId = resourceId.split("@")[0];
    const found = findItem(itemId);
    return found
      ? new Response(JSON.stringify(found), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      : new Response("not found", { status: 404 });
  }

  if (parsed.pathname === "/api/v1/commands/marketplace.preview") {
    const found = findItem(body?.resource_ref);
    if (!found) return new Response("not found", { status: 404 });
    const blocked = found.operation_state === "blocked";
    return new Response(
      JSON.stringify({
        id: found.id,
        type: "registry-preview",
        provider_id: "local-browser",
        artifact_sha256: found.integrity.sha256,
        item: found,
        route: found.route,
        activation_allowed: !blocked && found.route !== "manual" && found.route_available,
        findings: blocked
          ? [{ code: "incompatible_platform", severity: "error", message: "Host requirement is not satisfied" }]
          : [],
        decision: marketplaceDecision(found, blocked),
        security_notices: found.item_id === "example-plugin" ? ["Review filesystem write access"] : [],
        blockers: blocked ? ["Compatibility policy blocked installation"] : [],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }

  if (
    parsed.pathname === "/api/v1/commands/marketplace.install" ||
    parsed.pathname === "/api/v1/commands/marketplace.update"
  ) {
    if (mutationMode === "fail") {
      return errorResponse(500, "backend_error", "Marketplace owner mutation failed");
    }
    const found = findItem(body?.resource_ref);
    if (!found) return new Response("not found", { status: 404 });
    const action = parsed.pathname.endsWith("marketplace.update") ? "update" : "install";
    found.installed = true;
    found.installed_version = found.version;
    found.update_available = false;
    syncInstallation(found);
    return new Response(
      JSON.stringify({
        id: found.id,
        type: "marketplace-mutation",
        action,
        status: "applied",
        route: found.route,
        decision: { ...marketplaceDecision(found, false), operation: action },
        installation: found.installation,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }

  if (parsed.pathname === "/api/v1/commands/marketplace.uninstall") {
    const found = findItem(body?.resource_ref);
    if (!found) return new Response("not found", { status: 404 });
    found.installed = false;
    found.installed_version = null;
    found.update_available = false;
    found.pinned_version = null;
    syncInstallation(found);
    return new Response(
      JSON.stringify({
        id: found.item_id,
        type: "marketplace-mutation",
        action: "uninstall",
        status: "applied",
        route: found.route,
        decision: { ...marketplaceDecision(found, false), operation: "uninstall" },
        installation: null,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }

  if (
    parsed.pathname === "/api/v1/commands/registry.pin" ||
    parsed.pathname === "/api/v1/commands/registry.unpin"
  ) {
    const found = findItem(body?.resource_ref);
    if (!found) return new Response("not found", { status: 404 });
    found.pinned_version = parsed.pathname.endsWith("registry.pin") ? found.installed_version : null;
    syncInstallation(found);
    return new Response(
      JSON.stringify({
        id: found.item_id,
        type: "registry-installation",
        item_id: found.item_id,
        version: found.installed_version ?? found.version,
        pinned_version: found.pinned_version,
        source_registry: "local",
        source_repository: found.source.repository,
        package_reference: found.source.package_reference,
        revision: found.source.revision,
        license: found.license,
        provenance: found.provenance,
        history: [],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }

  return new Response("not found", { status: 404 });
};

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(<MarketplacePage client={new RegistryClient({ fetchImpl })} />);
