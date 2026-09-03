import { describe, expect, it, vi } from "vitest";

import canonicalTask from "./__fixtures__/canonical-task.json";
import { ControlPlaneClient } from "./client";

describe("canonical CLI/Web resource parity", () => {
  it("reads the shared canonical Task snapshot from the same versioned resource route", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(canonicalTask), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const observed = await client.getTask(canonicalTask.id);

    expect(observed).toEqual(canonicalTask);
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/tasks/${canonicalTask.id}`);
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");
  });
});
