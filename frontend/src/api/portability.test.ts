import { describe, expect, it, vi } from "vitest";
import { PortabilityClient } from "./portability";

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("PortabilityClient", () => {
  it("uses only the canonical portability collections for reads", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, next_cursor: null }));
    const client = new PortabilityClient({ fetchImpl });

    await client.listPackages({ limit: 10 });
    await client.listPreviews({ limit: 10 });
    await client.listReports({ limit: 10 });

    expect(fetchImpl.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/v1/portability-packages?limit=10",
      "/api/v1/portability-import-previews?limit=10",
      "/api/v1/portability-import-reports?limit=10",
    ]);
  });

  it("exports and validates through exact Control Plane commands", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ package_id: "package_1" }))
      .mockResolvedValueOnce(jsonResponse({ package_id: "package_1" }));
    const client = new PortabilityClient({ fetchImpl });

    await client.exportPackage(
      [{ resource_type: "agent", resource_id: "agent_1" }],
      { purpose: "browser" },
      "export-key",
    );
    await client.validatePackage(
      { manifest: {}, resources: [], checksum: "abc" },
      "validate-key",
    );

    const exportRequest = fetchImpl.mock.calls[0];
    const exportInit = exportRequest[1] as RequestInit;
    expect(String(exportRequest[0])).toBe("/api/v1/commands/portability.export");
    expect(new Headers(exportInit.headers).get("Idempotency-Key")).toBe("export-key");
    expect(JSON.parse(String(exportInit.body))).toEqual({
      resource_ref: "portability",
      resources: [{ resource_type: "agent", resource_id: "agent_1" }],
      metadata: { purpose: "browser" },
    });

    const validateRequest = fetchImpl.mock.calls[1];
    const validateInit = validateRequest[1] as RequestInit;
    expect(String(validateRequest[0])).toBe("/api/v1/commands/portability.package.validate");
    expect(new Headers(validateInit.headers).get("Idempotency-Key")).toBe("validate-key");
    expect(JSON.parse(String(validateInit.body))).toEqual({
      resource_ref: "portability",
      package: { manifest: {}, resources: [], checksum: "abc" },
    });
  });

  it("previews and imports by server-owned IDs without accepting a client mapping", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ preview_id: "preview_1", ready: true }))
      .mockResolvedValueOnce(jsonResponse({ report_id: "import_1", status: "succeeded" }));
    const client = new PortabilityClient({ fetchImpl });

    await client.previewPackage("package_1", "preview-key");
    await client.importPreview("preview_1", "import-key");

    const previewInit = fetchImpl.mock.calls[0][1] as RequestInit;
    expect(String(fetchImpl.mock.calls[0][0])).toBe("/api/v1/commands/portability.preview");
    expect(JSON.parse(String(previewInit.body))).toEqual({ resource_ref: "package_1" });

    const importInit = fetchImpl.mock.calls[1][1] as RequestInit;
    expect(String(fetchImpl.mock.calls[1][0])).toBe("/api/v1/commands/portability.import");
    expect(JSON.parse(String(importInit.body))).toEqual({ resource_ref: "preview_1" });
    expect(String(importInit.body)).not.toContain("id_mapping");
    expect(String(importInit.body)).not.toContain("import_order");
  });

  it("rejects empty canonical references before transport", async () => {
    const fetchImpl = vi.fn();
    const client = new PortabilityClient({ fetchImpl });

    expect(() => client.exportPackage([], undefined, "key")).toThrow();
    expect(() => client.previewPackage(" ", "key")).toThrow();
    expect(() => client.importPreview("", "key")).toThrow();
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
