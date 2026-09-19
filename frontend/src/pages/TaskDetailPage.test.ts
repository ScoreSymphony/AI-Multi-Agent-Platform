import { afterEach, describe, expect, it, vi } from "vitest";
import { confirmTaskCancellation } from "./TaskDetailPage";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("#1234 Task destructive action confirmation", () => {
  it("requires an explicit confirmation before Task cancellation", () => {
    const confirm = vi.fn((_message: string) => false);
    vi.stubGlobal("window", { confirm });

    expect(confirmTaskCancellation("Release task")).toBe(false);
    expect(confirm).toHaveBeenCalledOnce();
    expect(confirm.mock.calls[0]?.[0]).toContain('Cancel Task "Release task"');
    expect(confirm.mock.calls[0]?.[0]).toContain("Control Plane");
  });

  it("passes an explicit affirmative confirmation through", () => {
    const confirm = vi.fn((_message: string) => true);
    vi.stubGlobal("window", { confirm });

    expect(confirmTaskCancellation("Release task")).toBe(true);
  });
});
