import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CanonicalTask } from "../api/types";
import { RouterProvider } from "../app/router";
import { AssignedWorkTable } from "./AgentsPage";

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderWithRouter(children: ReactNode): string {
  vi.stubGlobal("window", {
    location: { pathname: "/agents/agent_test" },
    history: { pushState: vi.fn() },
    scrollTo: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  });
  return renderToStaticMarkup(<RouterProvider>{children}</RouterProvider>);
}

describe("#1234 Agent and Agent Team work relationships", () => {
  it("links assigned Tasks to their canonical Plan and Runs", () => {
    const task = {
      id: "task_test",
      title: "Investigate",
      status: "running",
      plan_ref: "plan_test",
      run_ids: ["run_one", "run_two"],
    } as CanonicalTask;

    const html = renderWithRouter(<AssignedWorkTable tasks={[task]} label="Agent" />);

    expect(html).toContain('href="/tasks/task_test"');
    expect(html).toContain('href="/plans/plan_test"');
    expect(html).toContain('href="/runs/run_one"');
    expect(html).toContain('href="/runs/run_two"');
  });

  it("renders an intentional empty state for assignments without Tasks", () => {
    const html = renderWithRouter(<AssignedWorkTable tasks={[]} label="Agent Team" />);
    expect(html).toContain("No Tasks assigned to this Agent Team");
  });
});
