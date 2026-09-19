import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ControlPlaneError } from "../../api/client";
import { describe, expect, it, vi } from "vitest";
import { TemplateClient, type CanonicalTemplate } from "../../api/templates";
import type { Page } from "../../api/types";
import { RouterProvider } from "../../app/router";
import { TemplateLibraryState, TemplatesPage } from "./TemplatesPage";

function retryableBackendError(message: string): ControlPlaneError {
  return new ControlPlaneError(503, {
    code: "unavailable",
    category: "backend",
    message,
    request_id: "request_regression_retry",
    correlation_id: "correlation_regression_retry",
    retryable: true,
  });
}

function page(items: CanonicalTemplate[] = []): Page<CanonicalTemplate> {
  return { items, next_cursor: null, total: items.length, limit: 50 };
}

function renderWithRouter(node: ReactNode): string {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { pathname: "/templates" }, history: { pushState: vi.fn() }, addEventListener: vi.fn(), removeEventListener: vi.fn() },
  });
  try {
    return renderToStaticMarkup(<RouterProvider>{node}</RouterProvider>);
  } finally {
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  }
}

describe("Templates route regression coverage", () => {
  it("preserves route composition, initial loading and extracted creation forms", () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("SSR regression coverage must not perform network requests");
    }) as unknown as typeof fetch;
    const html = renderWithRouter(<TemplatesPage client={new TemplateClient({ fetchImpl })} />);

    expect(html).toContain("<h1>Templates</h1>");
    expect(html).toContain("Template library");
    expect(html).toContain("Loading Templates…");
    expect(html).toContain("Create from an existing canonical resource");
    expect(html).toContain("Create from canonical Template JSON");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("maps loading, backend error and empty library states deterministically", () => {
    const retry = vi.fn();
    expect(renderToStaticMarkup(
      <TemplateLibraryState templates={null} error={null} onRetry={retry} />,
    )).toContain("Loading Templates…");

    const errorMarkup = renderToStaticMarkup(
      <TemplateLibraryState templates={null} error={retryableBackendError("template service unavailable")} onRetry={retry} />,
    );
    expect(errorMarkup).toContain('role="alert"');
    expect(errorMarkup).toContain("template service unavailable");
    expect(errorMarkup).toContain(">Retry</button>");

    const emptyMarkup = renderToStaticMarkup(
      <TemplateLibraryState templates={page()} error={null} onRetry={retry} />,
    );
    expect(emptyMarkup).toContain("No Templates yet");
    expect(emptyMarkup).toContain("Create one from canonical configuration or an existing resource.");

    const staleMarkup = renderToStaticMarkup(
      <TemplateLibraryState templates={page()} error={new Error("refresh failed")} onRetry={retry} />,
    );
    expect(staleMarkup).toContain("refresh failed");
    expect(staleMarkup).toContain("No Templates yet");
  });
});
