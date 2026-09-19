import { describe, expect, it, vi } from "vitest";
import { FilesClient } from "./files";

function response(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("FilesClient", () => {
  it("reads canonical file metadata only through /api/v1/files", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(response({ items: [], total: 0, next_cursor: null }))
      .mockResolvedValueOnce(response({ id: "file_1", type: "file" }));
    const client = new FilesClient({ fetchImpl });

    await client.listFiles({ limit: 25, filters: { project_id: "project_1" } });
    await client.getFile("file_1");

    expect(String(fetchImpl.mock.calls[0][0])).toBe(
      "/api/v1/files?limit=25&filter%5Bproject_id%5D=project_1",
    );
    expect(String(fetchImpl.mock.calls[1][0])).toBe("/api/v1/files/file_1");
    expect((fetchImpl.mock.calls[0][1] as RequestInit).method).toBe("GET");
    expect((fetchImpl.mock.calls[1][1] as RequestInit).method).toBe("GET");
  });

  it("does not invent a file-bytes or provider route", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(response({ id: "file_1", type: "file" }));
    const client = new FilesClient({ fetchImpl });

    await client.getFile("file_1");

    const url = String(fetchImpl.mock.calls[0][0]);
    expect(url).toBe("/api/v1/files/file_1");
    expect(url).not.toContain("download");
    expect(url).not.toContain("storage");
    expect(url).not.toContain("provider");
  });

  it("rejects an empty file ID before transport", async () => {
    const fetchImpl = vi.fn();
    const client = new FilesClient({ fetchImpl });

    expect(() => client.getFile(" ")).toThrow("File ID is required");
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
