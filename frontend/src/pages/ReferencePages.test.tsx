import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "../api/client";
import type { CanonicalRun } from "../api/types";
import { FilesClient } from "../api/files";
import { RouterProvider } from "../app/router";
import { ReferenceCollectionPage, ReferencesPage, StepRunTable } from "./ReferencePages";

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderWithRouter(children: ReactNode): string {
  vi.stubGlobal("window", {
    location: { pathname: "/files" },
    history: { pushState: vi.fn() },
    scrollTo: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  });
  return renderToStaticMarkup(<RouterProvider>{children}</RouterProvider>);
}

describe("Files & artifacts V1 surface", () => {
  it("renders canonical File metadata alongside Task-owned references", () => {
    const fetchImpl = vi.fn();
    const html = renderWithRouter(
      <ReferencesPage
        client={new ControlPlaneClient({ fetchImpl })}
        files={new FilesClient({ fetchImpl })}
      />,
    );

    expect(html).toContain("Files &amp; artifacts");
    expect(html).toContain("Canonical File metadata");
    expect(html).toContain("File bytes remain outside this metadata surface");
    expect(html).toContain("Loading File metadata");
    expect(html).toContain("Reference collection");
    expect(html).toContain("Artifacts");
    expect(html).toContain("Results");
    expect(html).toContain("Plans");
    expect(html).toContain("Steps");
    expect(html).not.toContain("file content is not a northbound resource yet");
  });

  it("renders stable collection overviews for deep-link and refresh entry", () => {
    const fetchImpl = vi.fn();
    const html = renderWithRouter(
      <ReferenceCollectionPage
        client={new ControlPlaneClient({ fetchImpl })}
        collection="plans"
      />,
    );

    expect(html).toContain("<h1>Plans</h1>");
    expect(html).toContain('href="/artifacts"');
    expect(html).toContain('href="/results"');
    expect(html).toContain('href="/plans"');
    expect(html).toContain('href="/steps"');
    expect(html).toContain('aria-current="page"');
    expect(html).toContain('href="/files"');
  });


  it("links a Step to the canonical Runs executed for it", () => {
    const run = {
      id: "run_step_test",
      task_id: "task_step_test",
      status: "succeeded",
      attempt: 2,
    } as CanonicalRun;

    const html = renderWithRouter(<StepRunTable runs={[run]} />);

    expect(html).toContain('href="/runs/run_step_test"');
    expect(html).toContain('href="/tasks/task_step_test"');
    expect(html).toContain("succeeded");
    expect(html).toContain(">2<");
  });

  it("renders an intentional empty state when a Step has no Runs", () => {
    const html = renderWithRouter(<StepRunTable runs={[]} />);
    expect(html).toContain("No Runs for this Step");
  });
});
