import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveConnectionStatus } from "../pages/TaskDetailPage";
import { Shell } from "./Shell";
import { RouterProvider } from "./router";

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderShell(pathname: string): string {
  vi.stubGlobal("window", {
    location: { pathname },
    history: { pushState: vi.fn() },
    scrollTo: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  });

  return renderToStaticMarkup(
    <RouterProvider>
      <Shell />
    </RouterProvider>,
  );
}

describe("#17 shell accessibility semantics", () => {
  it("connects navigation controls, marks the active route and exposes a focusable skip target", () => {
    const html = renderShell("/chat");

    expect(html).toContain('href="#main"');
    expect(html).toContain('id="platform-navigation"');
    expect(html).toContain('aria-controls="platform-navigation"');
    expect(html).toContain('aria-expanded="false"');
    expect((html.match(/aria-current="page"/g) ?? []).length).toBe(1);
    expect(html).toContain('href="/chat"');
    expect(html).toContain('id="main" tabindex="-1"');
  });

  it("announces Control Plane and Task live status changes politely", () => {
    const shell = renderShell("/chat");
    expect(shell).toContain('class="api-indicator" role="status" aria-live="polite"');

    const live = renderToStaticMarkup(<LiveConnectionStatus state="reconnecting" />);
    expect(live).toContain('role="status"');
    expect(live).toContain('aria-live="polite"');
    expect(live).toContain('aria-label="Live updates: reconnecting"');
    expect(live).toContain(">reconnecting</span>");
  });
});
