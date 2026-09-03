import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { APImanifest } from "../api/types";
import { LiveConnectionStatus } from "../pages/TaskDetailPage";
import { Shell, apiStatusLabel, manifestResourceState } from "./Shell";
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
    expect(shell).toContain("Checking API");
    expect(shell).not.toContain("API unavailable");

    const live = renderToStaticMarkup(<LiveConnectionStatus state="reconnecting" />);
    expect(live).toContain('role="status"');
    expect(live).toContain('aria-live="polite"');
    expect(live).toContain('aria-label="Live updates: reconnecting"');
    expect(live).toContain(">reconnecting</span>");
  });

  it("distinguishes API loading, ready and unavailable status text", () => {
    const manifest = { api_version: "v1" } as APImanifest;

    expect(apiStatusLabel("loading", null)).toBe("Checking API");
    expect(apiStatusLabel("ready", manifest)).toBe("/api/v1");
    expect(apiStatusLabel("unavailable", null)).toBe("API unavailable");
  });

  it("gates optional functional routes until the manifest is known", () => {
    const agents = renderShell("/agents");
    expect(agents).toContain("Checking Agents availability");
    expect(agents).not.toContain("Durable Agent definitions");

    const terminal = renderShell("/terminal");
    expect(terminal).toContain("Checking Terminal availability");
    expect(terminal).not.toContain("Canonical terminal sessions");
  });

  it("distinguishes advertised, absent and unavailable manifest resources", () => {
    const manifest = {
      api_version: "v1",
      resources: ["agents", "capabilities", "terminal-sessions"],
    } as APImanifest;

    expect(manifestResourceState("loading", null, "agents")).toBe("loading");
    expect(manifestResourceState("ready", manifest, "agents")).toBe("available");
    expect(manifestResourceState("ready", manifest, "agent-teams")).toBe("unavailable");
    expect(manifestResourceState("unavailable", null, "agents")).toBe("unavailable");
  });
});
