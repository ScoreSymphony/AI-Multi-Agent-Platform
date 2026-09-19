import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "../api/client";
import { RouterProvider } from "../app/router";
import {
  SearchPage,
  searchRequestFromQuery,
  searchRequestToQuery,
  searchResultPath,
} from "./SearchPage";

describe("SearchPage", () => {
  it("renders canonical global search filters including optional modes", () => {
    const fetchSpy = vi.fn();
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });
    const markup = renderToStaticMarkup(
      <RouterProvider>
        <SearchPage client={client} />
      </RouterProvider>,
    );

    expect(markup).toContain("Global search");
    expect(markup).toContain("Resource types");
    expect(markup).toContain("Updated after");
    expect(markup).toContain("semantic (optional)");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("round-trips supported filters through a refresh-safe route query", () => {
    const request = searchRequestFromQuery(
      "?q=browser&types=task%2Cresult&status=ignored&statuses=running%2Csucceeded"
      + "&project_id=project_1&mode=keyword&sort=relevance&direction=asc&limit=50",
    );
    expect(request).toMatchObject({
      q: "browser",
      types: ["task", "result"],
      statuses: ["running", "succeeded"],
      project_id: "project_1",
      mode: "keyword",
      sort: "relevance",
      direction: "asc",
      limit: 50,
    });

    const encoded = searchRequestToQuery(request);
    expect(encoded).toContain("q=browser");
    expect(encoded).toContain("types=task%2Cresult");
    expect(searchRequestFromQuery(encoded)).toEqual(request);
  });

  it("rejects invalid URL-owned filter enums and bounds instead of inventing client state", () => {
    const request = searchRequestFromQuery(
      "?mode=provider-private&sort=random&direction=sideways&limit=9999",
    );
    expect(request.mode).toBeUndefined();
    expect(request.sort).toBe("updated_at");
    expect(request.direction).toBe("desc");
    expect(request.limit).toBe(25);
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
    expect(searchResultPath({ resource_type: "research-item", resource_id: "research_1" })).toBe(
      "/research/research_1",
    );
    expect(searchResultPath({ resource_type: "decision-record", resource_id: "decision_1" })).toBe(
      "/decisions/decision_1",
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
