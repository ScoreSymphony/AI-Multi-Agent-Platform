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
    expect(searchResultPath({ resource_type: "unknown", resource_id: "unknown_1" })).toBeNull();
  });
});
