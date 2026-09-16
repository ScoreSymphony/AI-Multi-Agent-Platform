import { afterEach, describe, expect, it, vi } from "vitest";
import { csv, expiresAt, optional, required, splitCsv } from "./organizationFormInput";

afterEach(() => vi.useRealTimers());

describe("organization form input normalization", () => {
  it("trims required and optional values and rejects blank required input", () => {
    const form = new FormData();
    form.set("name", "  Platform Lab  ");
    form.set("display_name", "   ");

    expect(required(form, "name")).toBe("Platform Lab");
    expect(optional(form, "display_name")).toBeUndefined();
    expect(() => required(form, "missing")).toThrow("missing is required.");
  });

  it("normalizes comma-separated role and policy references without duplicates", () => {
    expect(splitCsv(" role:member, policy:read, role:member, , policy:read ")).toEqual([
      "role:member",
      "policy:read",
    ]);

    const form = new FormData();
    form.set("roles", " role:member, role:admin, role:member ");
    expect(csv(form, "roles")).toEqual(["role:member", "role:admin"]);
  });

  it("uses bounded invitation expiry durations", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-16T12:00:00Z"));

    const defaultForm = new FormData();
    expect(expiresAt(defaultForm)).toBe("2026-09-19T12:00:00.000Z");

    const bounded = new FormData();
    bounded.set("expires_in_hours", "1000");
    expect(expiresAt(bounded)).toBe("2026-10-16T12:00:00.000Z");

    const invalid = new FormData();
    invalid.set("expires_in_hours", "0");
    expect(expiresAt(invalid)).toBe("2026-09-19T12:00:00.000Z");
  });
});
