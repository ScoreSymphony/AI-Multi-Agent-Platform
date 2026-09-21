import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ControlPlaneClient } from "../api/client";
import { RouterProvider } from "../app/router";
import { ObservabilityPage, preserveTaskScopeDuringRecentTaskLoad } from "./ObservabilityPage";

function renderWithRouter(element: ReactElement): string {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { pathname: "/observability/task_older" } },
  });
  try {
    return renderToStaticMarkup(<RouterProvider>{element}</RouterProvider>);
  } finally {
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  }
}

describe("Observability task reachability", () => {
  it("does not let a late recent-Task load overwrite an edited empty draft", () => {
    expect(
      preserveTaskScopeDuringRecentTaskLoad(
        "",
        "task_11111111-1111-4111-8111-111111111111",
        true,
      ),
    ).toBe("");
  });

  it("auto-selects a recent Task only before the Task scope is edited", () => {
    expect(
      preserveTaskScopeDuringRecentTaskLoad(
        "",
        "task_11111111-1111-4111-8111-111111111111",
        false,
      ),
    ).toBe("task_11111111-1111-4111-8111-111111111111");
    expect(
      preserveTaskScopeDuringRecentTaskLoad(
        "task_22222222-2222-4222-8222-222222222222",
        "task_11111111-1111-4111-8111-111111111111",
        false,
      ),
    ).toBe("task_22222222-2222-4222-8222-222222222222");
  });

  it("keeps an exact Task ID reachable even when it is outside the recent Task inventory", () => {
    const html = renderWithRouter(
      <ObservabilityPage
        client={{} as ControlPlaneClient}
        view="observability"
        initialTaskId="task_older"
      />,
    );

    expect(html).toContain('value="task_older"');
    expect(html).toContain('href="/observability/task_older"');
    expect(html).toContain('href="/tasks/task_older"');
    expect(html).toContain("task_older");
  });
});
