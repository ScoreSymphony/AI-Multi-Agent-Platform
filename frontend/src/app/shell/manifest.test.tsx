import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { APImanifest } from "../../api/types";
import { ManifestResourcePage, ManifestResourcesPage } from "./manifest";

const manifest = {
  api_version: "v1",
  resources: ["tasks"],
  commands: [],
} as unknown as APImanifest;

describe("manifest-gated cross-cutting states", () => {
  it("delegates a global manifest outage to the shell error boundary", () => {
    const single = renderToStaticMarkup(
      <ManifestResourcePage
        state="unavailable"
        manifest={null}
        label="Tasks"
        resource="tasks"
      >
        <p>tasks</p>
      </ManifestResourcePage>,
    );
    const multiple = renderToStaticMarkup(
      <ManifestResourcesPage
        state="unavailable"
        manifest={null}
        label="Knowledge"
        resources={["knowledge", "knowledge-results"]}
      >
        <p>knowledge</p>
      </ManifestResourcesPage>,
    );

    expect(single).toBe("");
    expect(multiple).toBe("");
  });

  it("keeps a missing optional resource distinct once the manifest is available", () => {
    const markup = renderToStaticMarkup(
      <ManifestResourcePage
        state="ready"
        manifest={manifest}
        label="Models"
        resource="models"
      >
        <p>models</p>
      </ManifestResourcePage>,
    );
    expect(markup).toContain("Canonical subsystem unavailable");
  });

  it("keeps initial discovery as loading rather than outage", () => {
    const markup = renderToStaticMarkup(
      <ManifestResourcePage
        state="loading"
        manifest={null}
        label="Models"
        resource="models"
      >
        <p>models</p>
      </ManifestResourcePage>,
    );
    expect(markup).toContain("Checking Models availability");
  });
});
