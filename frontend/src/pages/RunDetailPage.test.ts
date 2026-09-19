import { afterEach, describe, expect, it, vi } from "vitest";
import { confirmRunCancellation } from "./Pages";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("#1234 Run destructive action confirmation", () => {
  it("requires explicit confirmation before cancelling a canonical Run", () => {
    const confirm = vi.fn((_message: string) => false);
    vi.stubGlobal("window", { confirm });

    expect(confirmRunCancellation("run_test")).toBe(false);
    expect(confirm).toHaveBeenCalledOnce();
    expect(confirm.mock.calls[0]?.[0]).toContain('Cancel Run "run_test"');
    expect(confirm.mock.calls[0]?.[0]).toContain("Control Plane");
  });

  it("passes affirmative confirmation through", () => {
    vi.stubGlobal("window", { confirm: vi.fn((_message: string) => true) });
    expect(confirmRunCancellation("run_test")).toBe(true);
  });
});
