import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "../api/client";
import { FilesClient } from "../api/files";
import { ReferencesPage } from "./ReferencePages";

describe("Files & artifacts V1 surface", () => {
  it("renders canonical File metadata alongside Task-owned references", () => {
    const fetchImpl = vi.fn();
    const html = renderToStaticMarkup(
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
});
