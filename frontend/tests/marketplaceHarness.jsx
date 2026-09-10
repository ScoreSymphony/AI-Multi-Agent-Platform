import React from "react";
import { createRoot } from "react-dom/client";
import { RegistryClient } from "/src/api/registry.ts";
import { MarketplacePage } from "/src/pages/MarketplacePage.tsx";

const calls = [];
window.__marketplaceCalls = calls;

const candidate = {
  id: "projectatlas@1.0.0",
  type: "registry-item",
  item_id: "projectatlas",
  item_type: "tool",
  name: "ProjectAtlas",
  description: "Repository intelligence candidate",
  version: "1.0.0",
  publisher: "example",
  source: {
    repository: "https://example.invalid/projectatlas",
    package_reference: "github:example/projectatlas",
    revision: null,
  },
  license: "MIT",
  provenance: "browser regression fixture",
  minimum_platform_version: null,
  maximum_platform_version: null,
  dependencies: [],
  requested_permissions: [],
  required_capabilities: [],
  required_plugins: [],
  required_connectors: [],
  required_models: [],
  tags: [
    "lifecycle:candidate",
    "evaluation:required",
    "deployment:local",
    "cost:compatible",
    "network:optional",
  ],
  categories: ["code-intelligence"],
  trust_status: "untrusted",
  review_reference: "https://example.invalid/review",
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
};

const fetchImpl = async (input) => {
  const url = String(input);
  calls.push(url);
  const parsed = new URL(url, window.location.origin);

  if (parsed.pathname === "/api/v1/registry-items") {
    return new Response(
      JSON.stringify({ items: [candidate], next_cursor: null, total: 1, limit: 50 }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }

  return new Response("not found", { status: 404 });
};

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(<MarketplacePage client={new RegistryClient({ fetchImpl })} />);
