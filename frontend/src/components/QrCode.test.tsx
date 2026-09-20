import { describe, expect, it } from "vitest";

import { encodeQr } from "./QrCode";

describe("QrCode", () => {
  it("encodes a compact payload into a standards-sized QR matrix with finder patterns", () => {
    const matrix = encodeQr("aiagentplatform://pair?v=1&origin=https%3A%2F%2Flocalhost&code=ABC");
    expect(matrix.length).toBeGreaterThanOrEqual(21);
    expect((matrix.length - 17) % 4).toBe(0);
    expect(matrix.every((row) => row.length === matrix.length)).toBe(true);

    // Finder centers and their rings are fixed function modules.
    expect(matrix[3]?.[3]).toBe(true);
    expect(matrix[3]?.[2]).toBe(true);
    expect(matrix[3]?.[1]).toBe(false);
    expect(matrix[3]?.[matrix.length - 4]).toBe(true);
    expect(matrix[matrix.length - 4]?.[3]).toBe(true);
  });

  it("rejects payloads beyond the bounded local encoder capacity", () => {
    expect(() => encodeQr("x".repeat(1000))).toThrow("exceeds supported local QR capacity");
  });
});
