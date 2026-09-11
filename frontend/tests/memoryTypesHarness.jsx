import React from "react";
import { createRoot } from "react-dom/client";
import { MemoryKnowledgeClient } from "/src/api/memoryKnowledge.ts";
import { matchPath, RouterProvider, useRouter } from "/src/app/router.tsx";
import { MemoryDetailPage, MemoryPage } from "/src/pages/MemoryKnowledgePages.tsx";

const calls = [];
const memories = new Map();
let sequence = 0;

window.__memoryTypeCalls = calls;
window.__memoryTypeState = { memories };

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function createCanonicalMemory(payload, overrides = {}) {
  sequence += 1;
  const id = overrides.id ?? `memory-browser-${sequence}`;
  return {
    id,
    type: "memory",
    scope: payload.scope,
    scope_id: payload.scope_id,
    project_id: payload.project_id ?? null,
    owner_ref: "user:browser-regression",
    created_by: "user:browser-regression",
    created_at: `2026-09-11T09:${String(sequence).padStart(2, "0")}:00+00:00`,
    value: payload.value ?? {},
    origin: payload.origin ?? "user-authored",
    memory_type: payload.memory_type,
    retention: payload.retention ?? (payload.scope === "short_term" ? "ephemeral" : "durable"),
    expires_at: payload.expires_at ?? null,
    provenance: payload.provenance ?? [],
    supersedes_memory_id: overrides.supersedes_memory_id ?? null,
    superseded_by_memory_id: overrides.superseded_by_memory_id ?? null,
    classification: payload.classification ?? null,
    metadata: payload.metadata ?? {},
  };
}

const fetchImpl = async (input, init = {}) => {
  const parsed = new URL(String(input), window.location.origin);
  const method = init.method ?? "GET";
  const body = init.body ? JSON.parse(String(init.body)) : null;
  calls.push({ method, url: `${parsed.pathname}${parsed.search}`, body });

  if (method === "GET" && parsed.pathname === "/api/v1/memory") {
    let items = [...memories.values()];
    const scope = parsed.searchParams.get("filter[scope]");
    const scopeId = parsed.searchParams.get("filter[scope_id]");
    const projectId = parsed.searchParams.get("filter[project_id]");
    const memoryType = parsed.searchParams.get("filter[memory_type]");
    const search = parsed.searchParams.get("q")?.toLowerCase() ?? null;

    if (scope) items = items.filter((entry) => entry.scope === scope);
    if (scopeId) items = items.filter((entry) => entry.scope_id === scopeId);
    if (projectId) items = items.filter((entry) => entry.project_id === projectId);
    if (memoryType) items = items.filter((entry) => entry.memory_type === memoryType);
    if (search) {
      items = items.filter((entry) => JSON.stringify(entry.value).toLowerCase().includes(search));
    }

    return jsonResponse({ items, next_cursor: null, total: items.length, limit: 50 });
  }

  if (method === "GET" && parsed.pathname.startsWith("/api/v1/memory/")) {
    const id = decodeURIComponent(parsed.pathname.slice("/api/v1/memory/".length));
    const entry = memories.get(id);
    return entry ? jsonResponse(entry) : jsonResponse({ message: "not found" }, 404);
  }

  if (method === "POST" && parsed.pathname === "/api/v1/commands/memory.create") {
    if (!body?.memory_type) {
      throw new Error(`memory.create omitted canonical memory_type: ${JSON.stringify(body)}`);
    }
    const entry = createCanonicalMemory(body);
    memories.set(entry.id, entry);
    return jsonResponse(entry);
  }

  if (method === "POST" && parsed.pathname === "/api/v1/commands/memory.update") {
    const source = memories.get(body?.resource_ref);
    if (!source) return jsonResponse({ message: "not found" }, 404);
    if (Object.prototype.hasOwnProperty.call(body, "memory_type")) {
      throw new Error(`memory.update attempted to mutate memory_type: ${JSON.stringify(body)}`);
    }
    const replacement = createCanonicalMemory(
      {
        scope: source.scope,
        scope_id: source.scope_id,
        project_id: source.project_id,
        value: body.value ?? source.value,
        origin: source.origin,
        memory_type: source.memory_type,
        retention: body.retention ?? source.retention,
        expires_at: body.expires_at ?? source.expires_at,
        provenance: source.provenance,
        classification: body.classification ?? source.classification,
        metadata: body.metadata ?? source.metadata,
      },
      { supersedes_memory_id: source.id },
    );
    memories.set(source.id, { ...source, superseded_by_memory_id: replacement.id });
    memories.set(replacement.id, replacement);
    return jsonResponse(replacement);
  }

  if (method === "POST" && parsed.pathname === "/api/v1/commands/memory.promote") {
    const source = memories.get(body?.resource_ref);
    if (!source) return jsonResponse({ message: "not found" }, 404);
    if (Object.prototype.hasOwnProperty.call(body, "memory_type")) {
      throw new Error(`memory.promote attempted to mutate memory_type: ${JSON.stringify(body)}`);
    }
    const promoted = createCanonicalMemory({
      scope: body.scope,
      scope_id: body.scope_id,
      project_id: body.project_id ?? null,
      value: source.value,
      origin: source.origin,
      memory_type: source.memory_type,
      retention: body.retention ?? "durable",
      expires_at: body.expires_at ?? null,
      provenance: [...source.provenance, { kind: "memory", ref: source.id, location: null, revision: null, checksum: null }],
      classification: source.classification,
      metadata: source.metadata,
    });
    memories.set(promoted.id, promoted);
    return jsonResponse(promoted);
  }

  return jsonResponse({ message: `unhandled ${method} ${parsed.pathname}` }, 404);
};

const client = new MemoryKnowledgeClient({ fetchImpl });

function MemoryHarnessRoutes() {
  const { path } = useRouter();
  const memoryMatch = matchPath("/memory/:memoryId", path);
  if (memoryMatch) {
    return <MemoryDetailPage client={client} memoryId={memoryMatch.memoryId} />;
  }
  return <MemoryPage client={client} />;
}

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <RouterProvider>
    <MemoryHarnessRoutes />
  </RouterProvider>,
);
