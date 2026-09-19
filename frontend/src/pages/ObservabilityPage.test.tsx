import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ControlPlaneClient } from "../api/client";
import { RouterProvider } from "../app/router";
import { ObservabilityPage } from "./ObservabilityPage";

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
