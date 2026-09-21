import { afterEach, describe, expect, it, vi } from "vitest";
import { createUuid } from "./uuid";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createUuid", () => {
  it("uses native crypto.randomUUID when available", () => {
    const randomUUID = vi.fn(() => "123e4567-e89b-42d3-a456-426614174000");
    vi.stubGlobal("crypto", {
      randomUUID,
      getRandomValues: vi.fn(),
    });

    expect(createUuid()).toBe("123e4567-e89b-42d3-a456-426614174000");
    expect(randomUUID).toHaveBeenCalledTimes(1);
  });

  it("falls back to crypto.getRandomValues when randomUUID is unavailable", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(0);
        return bytes;
      },
    });

    expect(createUuid()).toBe("00000000-0000-4000-8000-000000000000");
  });

  it("does not fall back to an insecure pseudo-random source", () => {
    vi.stubGlobal("crypto", {});

    expect(() => createUuid()).toThrow("Secure random UUID generation is unavailable");
  });
});
