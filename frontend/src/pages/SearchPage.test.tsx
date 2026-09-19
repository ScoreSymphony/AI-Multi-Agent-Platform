import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "../api/client";
import { SearchPage, searchResultPath } from "./SearchPage";

describe("SearchPage", () => {
  it("renders canonical global search filters including optional modes", () => {
    const fetchSpy = vi.fn();
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });
    const markup = renderToStaticMarkup(<SearchPage client={client} />);

    expect(markup).toContain("Global search");
    expect(markup).toContain("Resource types");
    expect(markup).toContain("Updated after");
    expect(markup).toContain("semantic (optional)");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("navigates known result types to their canonical UI resource routes", () => {
    expect(searchResultPath({ resource_type: "project", resource_id: "project_1" })).toBe(
      "/projects/project_1",
    );
    expect(searchResultPath({ resource_type: "workspace", resource_id: "workspace_1" })).toBe(
      "/workspaces/workspace_1",
    );
    expect(searchResultPath({ resource_type: "task", resource_id: "task_1" })).toBe(
      "/tasks/task_1",
    );
    expect(searchResultPath({ resource_type: "run", resource_id: "run_1" })).toBe(
      "/runs/run_1",
    );
    expect(searchResultPath({ resource_type: "file", resource_id: "file_1" })).toBe(
      "/files/file_1",
    );
    expect(searchResultPath({ resource_type: "memory", resource_id: "memory_1" })).toBe(
      "/memory/memory_1",
    );
    expect(
      searchResultPath({ resource_type: "knowledge-source", resource_id: "knowledge_1" }),
    ).toBe("/knowledge/knowledge_1");
    expect(searchResultPath({ resource_type: "capability", resource_id: "tool/1" })).toBe(
      "/tools/tool%2F1",
    );
    expect(
      searchResultPath({ resource_type: "capability-provider", resource_id: "mcp/provider" }),
    ).toBe("/tools/providers/mcp%2Fprovider");
    expect(searchResultPath({ resource_type: "model", resource_id: "model_1" })).toBe(
      "/models/model_1",
    );
    expect(
      searchResultPath({ resource_type: "model-provider", resource_id: "provider_1" }),
    ).toBe("/models/providers/provider_1");
    expect(searchResultPath({ resource_type: "node", resource_id: "node_1" })).toBe(
      "/compute/nodes/node_1",
    );
    expect(searchResultPath({ resource_type: "worker", resource_id: "worker_1" })).toBe(
      "/compute/workers/worker_1",
    );
    expect(searchResultPath({ resource_type: "approval", resource_id: "approval_1" })).toBe(
      "/approvals/approval_1",
    );
    expect(searchResultPath({ resource_type: "unknown", resource_id: "unknown_1" })).toBeNull();
  });
});
