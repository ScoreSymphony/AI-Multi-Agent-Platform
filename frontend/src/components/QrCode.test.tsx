import { describe, expect, it } from "vitest";

import { qrMatrix } from "./QrCode";

describe("pairing QR renderer", () => {
  it("renders a square version-1 matrix for a short byte payload", () => {
    const matrix = qrMatrix("HELLO");

    expect(matrix).toHaveLength(21);
    expect(matrix.every((row) => row.length === 21)).toBe(true);
    expect(matrix[0][0]).toBe(true);
    expect(matrix[6][6]).toBe(true);
    expect(matrix[0][20]).toBe(true);
    expect(matrix[20][0]).toBe(true);
  });

  it("selects larger QR versions for realistic pairing deep links", () => {
    const matrix = qrMatrix(
      "aiagentplatform://pair?v=1&origin=https%3A%2F%2Fplatform.example&request_id=pairing_1234567890&secret=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMN",
    );

    expect(matrix.length).toBeGreaterThan(21);
    expect((matrix.length - 17) % 4).toBe(0);
  });

  it("fails closed when the payload exceeds the supported local renderer", () => {
    expect(() => qrMatrix("x".repeat(400))).toThrow("Pairing QR payload is too large");
  });
});
