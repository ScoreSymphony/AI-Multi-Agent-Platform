import type { AnchorHTMLAttributes } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { CanonicalMemoryEntry } from "../api/memoryKnowledge";
import {
  MemoryCreateForm,
  MemoryPromoteForm,
  MemoryTable,
  MemoryTypeDetail,
  MemoryUpdateForm,
  buildMemoryQueryKey,
  memoryTypeFilterValue,
} from "./MemoryKnowledgePages";

vi.mock("../app/router", () => ({
  AppLink: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

function memory(overrides: Partial<CanonicalMemoryEntry> = {}): CanonicalMemoryEntry {
  return {
    id: "memory-744",
    type: "memory",
    scope: "user",
    scope_id: "user-a",
    project_id: null,
    owner_ref: "user:user-a",
    created_by: "user:user-a",
    created_at: "2026-09-11T09:00:00+00:00",
    value: { workflow: "compile-release" },
    origin: "user-authored",
    memory_type: "procedural",
    retention: "durable",
    expires_at: null,
    provenance: [],
    supersedes_memory_id: null,
    superseded_by_memory_id: null,
    classification: null,
    metadata: {},
    ...overrides,
  };
}

describe("Memory UI canonical Memory Type support", () => {
  it("maps the all-types choice to no backend filter", () => {
    expect(memoryTypeFilterValue("all")).toBeUndefined();
    expect(memoryTypeFilterValue("procedural")).toBe("procedural");
  });

  it("includes Memory Type in the inventory query identity", () => {
    const base = {
      scope: "user" as const,
      scopeId: "user-a",
      projectId: "",
      search: "release",
      includeExpired: false,
      includeSuperseded: false,
    };

    const allTypes = buildMemoryQueryKey({ ...base, memoryType: "all" });
    const procedural = buildMemoryQueryKey({ ...base, memoryType: "procedural" });

    expect(allTypes).not.toBe(procedural);
    expect(procedural).toContain("procedural");
  });

  it("renders Memory Type as a first-class inventory column", () => {
    const html = renderToStaticMarkup(<MemoryTable entries={[memory()]} />);

    expect(html).toContain("<th>Type</th>");
    expect(html).toContain("procedural");
  });

  it("renders Memory Type explicitly in detail metadata", () => {
    const html = renderToStaticMarkup(<dl><MemoryTypeDetail entry={memory()} /></dl>);

    expect(html).toContain("Memory Type");
    expect(html).toContain("procedural");
  });

  it("requires an explicit semantic or compatibility type when creating Memory", () => {
    const html = renderToStaticMarkup(
      <MemoryCreateForm disabled={false} onSubmit={async () => undefined} />,
    );

    expect(html).toContain("Memory Type");
    expect(html).toContain("Select a Memory Type");
    expect(html).toContain("episodic");
    expect(html).toContain("semantic");
    expect(html).toContain("procedural");
    expect(html).toContain("preference");
    expect(html).toContain("reflective");
    expect(html).toContain("unclassified (legacy/import compatibility)");
    expect(html).toContain("required");
  });

  it("does not expose Memory Type mutation through ordinary update or promotion forms", () => {
    const updateHtml = renderToStaticMarkup(
      <MemoryUpdateForm
        entry={memory()}
        disabled={false}
        onSubmit={async () => undefined}
      />,
    );
    const promoteHtml = renderToStaticMarkup(
      <MemoryPromoteForm disabled={false} onSubmit={async () => undefined} />,
    );

    expect(updateHtml).not.toContain("Memory Type");
    expect(promoteHtml).not.toContain("Memory Type");
  });
});
