import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RouterProvider } from "../app/router";
import { NotFoundPage } from "./Pages";

describe("cross-cutting route not-found state", () => {
  it("keeps unknown routes distinct from optional subsystem unavailability", () => {
    const markup = renderToStaticMarkup(
      <RouterProvider>
        <NotFoundPage path="/definitely-missing" />
      </RouterProvider>,
    );
    expect(markup).toContain("Page not found");
    expect(markup).toContain("Unknown route");
    expect(markup).toContain("Return to platform overview");
    expect(markup).not.toContain("Canonical subsystem unavailable");
  });
});
