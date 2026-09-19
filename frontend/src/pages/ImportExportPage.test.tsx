import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { PortabilityClient } from "../api/portability";
import {
  ImportExportPage,
  parsePackageDocument,
  portabilitySelection,
} from "./ImportExportPage";

describe("ImportExportPage", () => {
  it("renders the canonical package -> preview -> import workflow", () => {
    const client = new PortabilityClient({ fetchImpl: vi.fn() });
    const html = renderToStaticMarkup(<ImportExportPage client={client} />);

    expect(html).toContain("Import / Export");
    expect(html).toContain("Export canonical resource");
    expect(html).toContain("Validate incoming package");
    expect(html).toContain("Portable packages");
    expect(html).toContain("Import previews");
    expect(html).toContain("Import reports");
    expect(html).toContain("Import plans remain server-owned");
    expect(html).toContain("Create portable package");
    expect(html).toContain("Validate package");
  });

  it("normalizes explicit resource selections", () => {
    expect(portabilitySelection(" agent ", " agent_1 ")).toEqual({
      resource_type: "agent",
      resource_id: "agent_1",
    });
    expect(() => portabilitySelection("", "agent_1")).toThrow("Resource type is required");
    expect(() => portabilitySelection("agent", "")).toThrow("Resource ID is required");
  });

  it("accepts only JSON object package documents", () => {
    expect(parsePackageDocument('{"checksum":"abc"}')).toEqual({ checksum: "abc" });
    expect(() => parsePackageDocument("not-json")).toThrow("valid JSON");
    expect(() => parsePackageDocument("[]")).toThrow("JSON object");
  });
});
