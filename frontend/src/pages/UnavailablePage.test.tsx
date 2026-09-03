import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { APImanifest } from "../api/types";
import { UnavailablePage } from "./Pages";

const manifest: APImanifest = {
  api_version: "v1",
  resources: ["tasks", "tools"],
  commands: [],
  openapi: "/api/v1/openapi.json",
  live_updates: "sse",
};

describe("optional subsystem states", () => {
  it("renders an explicit unavailable state when no canonical API is registered", () => {
    const markup = renderToStaticMarkup(
      <UnavailablePage item={{ label: "Tools", apiResource: "tools" }} manifest={null} />,
    );
    expect(markup).toContain("Canonical subsystem unavailable");
    expect(markup).toContain("No private backend fallback is used");
  });

  it("distinguishes registered API from a UI integration that is still pending", () => {
    const markup = renderToStaticMarkup(
      <UnavailablePage item={{ label: "Tools", apiResource: "tools" }} manifest={manifest} />,
    );
    expect(markup).toContain("UI integration pending");
    expect(markup).toContain("Control Plane advertises tools");
  });
});
